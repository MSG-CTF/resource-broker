locals {
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
    for binding in setproduct(var.target_project_ids, local.project_roles) :
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
  for_each = var.target_project_ids

  project            = each.value
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "osconfig" {
  for_each = var.target_project_ids

  project            = each.value
  service            = "osconfig.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbilling" {
  for_each = var.target_project_ids

  project            = each.value
  service            = "cloudbilling.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "bigquery" {
  for_each = var.target_project_ids

  project            = each.value
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}

# VM Manager requires a Google-managed OS Config Service Agent in each target
# project. This role belongs only to that Google-managed identity, never to the
# central Broker service account.
resource "google_project_service_identity" "osconfig" {
  provider = google-beta
  for_each = var.target_project_ids

  project = google_project_service.osconfig[each.value].project
  service = "osconfig.googleapis.com"
}

resource "google_project_iam_member" "osconfig_service_agent" {
  for_each = var.target_project_ids

  project = each.value
  role    = "roles/osconfig.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.osconfig[each.value].email}"
}

# Activate the OS Config agent for VMs that inherit project metadata. Recent
# supported Google images already include the agent; older/custom images still
# need the agent installed in the image or through a one-time bootstrap.
resource "google_compute_project_metadata_item" "enable_osconfig" {
  for_each = var.target_project_ids

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
  for_each = var.target_project_ids

  project = each.value
  role    = "roles/iam.serviceAccountUser"
  member  = local.central_service_account_member
}

# Billing access is managed on Cloud Billing accounts, not on projects. This
# read-only role prepares the Broker to read cost information in a later change.
resource "google_billing_account_iam_member" "central_broker_billing_viewer" {
  for_each = var.target_billing_account_ids

  billing_account_id = each.value
  role               = "roles/billing.viewer"
  member             = local.central_service_account_member
}
