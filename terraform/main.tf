locals {
  target_project_ids = toset(keys(var.projects))
  target_billing_account_ids = toset([
    for project in values(var.projects) : project.billing_account_id
  ])
  billing_iam_account_ids = var.manage_billing_iam ? local.target_billing_account_ids : toset([])

  # These roles are mandatory for every managed project. Compute Admin covers
  # Compute Engine lifecycle, while the OS Config roles support the Broker's
  # provider-native Bootstrap flow.
  project_roles = toset([
    "roles/compute.admin",
    "roles/osconfig.osPolicyAssignmentAdmin",
    "roles/osconfig.osPolicyAssignmentReportViewer",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/bigquery.dataViewer",
    "roles/bigquery.jobUser",
  ])

  project_role_bindings = {
    for binding in setproduct(local.target_project_ids, local.project_roles) :
    "${binding[0]} ${binding[1]}" => {
      project_id = binding[0]
      role       = binding[1]
    }
  }

  central_service_account_member = "serviceAccount:${var.central_service_account_email}"
}

# Enable every Google API used by the current Broker flow and the planned
# BigQuery-based cost reader. Keeping disable_on_destroy=false prevents a
# Terraform destroy from disabling shared APIs used by existing workloads.
resource "google_project_service" "compute" {
  for_each = local.target_project_ids

  project            = each.value
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "osconfig" {
  for_each = local.target_project_ids

  project            = each.value
  service            = "osconfig.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbilling" {
  for_each = local.target_project_ids

  project            = each.value
  service            = "cloudbilling.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "bigquery" {
  for_each = local.target_project_ids

  project            = each.value
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}

# VM Manager requires a Google-managed OS Config Service Agent in each target
# project. This role belongs only to that Google-managed identity, never to the
# central Broker service account.
resource "google_project_service_identity" "osconfig" {
  provider = google-beta
  for_each = local.target_project_ids

  project = google_project_service.osconfig[each.value].project
  service = "osconfig.googleapis.com"
}

resource "google_project_iam_member" "osconfig_service_agent" {
  for_each = local.target_project_ids

  project = each.value
  role    = "roles/osconfig.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.osconfig[each.value].email}"
}

# Activate the OS Config agent for VMs that inherit project metadata. Recent
# supported Google images already include the agent; older/custom images still
# need the agent installed in the image or through a one-time bootstrap.
resource "google_compute_project_metadata_item" "enable_osconfig" {
  for_each = local.target_project_ids

  project = each.value
  key     = "enable-osconfig"
  value   = "TRUE"

  depends_on = [
    google_project_service.compute,
    google_project_service.osconfig,
  ]
}

# google_project_iam_member is additive: it preserves unrelated IAM members and
# roles already present in each target project.
resource "google_project_iam_member" "central_broker" {
  for_each = local.project_role_bindings

  project = each.value.project_id
  role    = each.value.role
  member  = local.central_service_account_member

  depends_on = [
    google_project_service.compute,
    google_project_service.osconfig,
    google_project_service.cloudbilling,
    google_project_service.bigquery,
  ]
}

# Project-wide actAs is mandatory for the requested onboarding baseline so the
# Broker can create or update VMs that attach project-owned service accounts.
resource "google_project_iam_member" "central_broker_service_account_user" {
  for_each = local.target_project_ids

  project = each.value
  role    = "roles/iam.serviceAccountUser"
  member  = local.central_service_account_member
}

# Billing IAM is opt-in because project onboarding does not require permission
# to manage access to the linked Billing accounts.
resource "google_billing_account_iam_member" "central_broker_billing_viewer" {
  for_each = local.billing_iam_account_ids

  billing_account_id = each.value
  role               = "roles/billing.viewer"
  member             = local.central_service_account_member
}

locals {
  problem_vm_specs = {
    heavy = {
      name              = "msg-problem-heavy-01"
      machine_type      = "e2-standard-8"
      boot_disk_size_gb = 100
    }
    light = {
      name              = "msg-problem-light-01"
      machine_type      = "e2-standard-4"
      boot_disk_size_gb = 100
    }
    light-02 = {
      name              = "msg-problem-light-02"
      machine_type      = "e2-standard-2"
      boot_disk_size_gb = 50
    }
    light-03 = {
      name              = "msg-problem-light-03"
      machine_type      = "e2-standard-2"
      boot_disk_size_gb = 50
    }
  }

  problem_vms = {
    for pair in setproduct(local.target_project_ids, keys(local.problem_vm_specs)) :
    "${pair[0]}/${pair[1]}" => {
      project_id        = pair[0]
      name              = local.problem_vm_specs[pair[1]].name
      machine_type      = local.problem_vm_specs[pair[1]].machine_type
      boot_disk_size_gb = local.problem_vm_specs[pair[1]].boot_disk_size_gb
    }
  }
}

data "google_compute_default_service_account" "problems" {
  for_each = local.target_project_ids

  project = google_project_service.compute[each.value].project
}

resource "google_compute_network" "problems" {
  for_each = local.target_project_ids

  project                 = google_project_service.compute[each.value].project
  name                    = "msg-problems-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "problems" {
  for_each = var.projects

  project = each.key
  name    = "msg-problems-subnet"
  region  = each.value.region
  network = google_compute_network.problems[each.key].id

  # Each project has an isolated VPC; avoid K3s pod/service CIDRs 10.42/10.43.
  ip_cidr_range = "10.80.0.0/24"
}

resource "google_compute_firewall" "problems_public" {
  for_each = local.target_project_ids

  project       = each.value
  name          = "msg-problems-public"
  network       = google_compute_network.problems[each.value].id
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["msg-problem"]

  # The requested challenge network exposes all TCP/UDP ports for random ports.
  allow {
    protocol = "tcp"
    ports    = ["1-65535"]
  }

  allow {
    protocol = "udp"
    ports    = ["1-65535"]
  }
}

resource "google_compute_address" "problems" {
  for_each = local.problem_vms

  project      = google_project_service.compute[each.value.project_id].project
  name         = "${each.value.name}-ip"
  region       = var.projects[each.value.project_id].region
  address_type = "EXTERNAL"
}

resource "google_compute_instance" "problems" {
  for_each = local.problem_vms

  project        = each.value.project_id
  name           = each.value.name
  zone           = var.projects[each.value.project_id].zone
  machine_type   = each.value.machine_type
  desired_status = var.problem_vms_running ? "RUNNING" : "TERMINATED"
  tags           = ["msg-problem"]

  boot_disk {
    auto_delete = true

    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = each.value.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.problems[each.value.project_id].id

    access_config {
      nat_ip = google_compute_address.problems[each.key].address
    }
  }

  service_account {
    email  = data.google_compute_default_service_account.problems[each.value.project_id].email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
}
