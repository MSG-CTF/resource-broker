locals {
  sla_ssh_public_key = trimspace(file("${path.module}/sla_to_control.pub"))

  all_vm_specs = {
    card-battle-arena = {
      project_group = "shared"
      machine_type  = "e2-custom-2-4096"
      boot_gib      = 30
      health_port   = true
    }
    jenga = {
      project_group = "shared"
      machine_type  = "e2-custom-2-4096"
      boot_gib      = 30
      health_port   = false
    }
    pixel-smuggler = {
      project_group = "pixel_grand"
      machine_type  = "e2-custom-2-4096"
      boot_gib      = 30
      health_port   = false
    }
    swanopoly = {
      project_group = "shared"
      machine_type  = "e2-highcpu-8"
      boot_gib      = 50
      health_port   = true
    }
    dependency-hell = {
      project_group = "heavy"
      machine_type  = "e2-highcpu-8"
      boot_gib      = 50
      health_port   = true
    }
    dependency-hell-runner = {
      project_group = "heavy"
      machine_type  = "e2-highcpu-4"
      boot_gib      = 30
      health_port   = false
    }
    grand-tour-duel = {
      project_group = "pixel_grand"
      machine_type  = "e2-highcpu-8"
      boot_gib      = 50
      health_port   = true
    }
  }

  project_ids = {
    for group, project_id in var.project_ids : group => project_id
    if contains(var.enabled_project_groups, group)
  }

  vm_specs = {
    for challenge, spec in local.all_vm_specs : challenge => spec
    if contains(var.enabled_project_groups, spec.project_group)
    && (spec.project_group != "shared" || contains(var.enabled_shared_challenges, challenge))
  }

  labels = {
    managed-by = "terraform"
    purpose    = "msg-koth"
  }

  dependency_node_tags = {
    dependency-hell        = ["msg-koth-dependency-control"]
    dependency-hell-runner = ["msg-koth-dependency-runner"]
  }
}

resource "google_project_service" "compute" {
  for_each = local.project_ids

  project            = each.value
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_compute_network" "koth" {
  for_each = local.project_ids

  project                 = google_project_service.compute[each.key].project
  name                    = "msg-koth-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "koth" {
  for_each = local.project_ids

  project                  = each.value
  name                     = "msg-koth-subnet"
  region                   = "us-central1"
  network                  = google_compute_network.koth[each.key].id
  ip_cidr_range            = "10.80.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_firewall" "participant" {
  for_each = local.project_ids

  project       = each.value
  name          = "msg-koth-participant"
  network       = google_compute_network.koth[each.key].id
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["msg-koth-participant"]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}

resource "google_compute_firewall" "health" {
  for_each = local.project_ids

  project       = each.value
  name          = "msg-koth-health"
  network       = google_compute_network.koth[each.key].id
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["msg-koth-health"]

  allow {
    protocol = "tcp"
    ports    = ["9090"]
  }
}

resource "google_compute_firewall" "ssh" {
  for_each = local.project_ids

  project       = each.value
  name          = "msg-koth-ssh"
  network       = google_compute_network.koth[each.key].id
  direction     = "INGRESS"
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["msg-koth"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_firewall" "dependency_k3s_api" {
  count = contains(var.enabled_project_groups, "heavy") ? 1 : 0

  project     = var.project_ids.heavy
  name        = "msg-koth-dependency-k3s-api"
  network     = google_compute_network.koth["heavy"].id
  direction   = "INGRESS"
  source_tags = ["msg-koth-dependency-runner"]
  target_tags = ["msg-koth-dependency-control"]

  allow {
    protocol = "tcp"
    ports    = ["6443"]
  }
}

resource "google_compute_firewall" "dependency_overlay" {
  count = contains(var.enabled_project_groups, "heavy") ? 1 : 0

  project     = var.project_ids.heavy
  name        = "msg-koth-dependency-overlay"
  network     = google_compute_network.koth["heavy"].id
  direction   = "INGRESS"
  source_tags = ["msg-koth-dependency-control", "msg-koth-dependency-runner"]
  target_tags = ["msg-koth-dependency-control", "msg-koth-dependency-runner"]

  allow {
    protocol = "udp"
    ports    = ["8472"]
  }
}

resource "google_compute_firewall" "dependency_kubelet" {
  count = contains(var.enabled_project_groups, "heavy") ? 1 : 0

  project     = var.project_ids.heavy
  name        = "msg-koth-dependency-kubelet"
  network     = google_compute_network.koth["heavy"].id
  direction   = "INGRESS"
  source_tags = ["msg-koth-dependency-control", "msg-koth-dependency-runner"]
  target_tags = ["msg-koth-dependency-control", "msg-koth-dependency-runner"]

  allow {
    protocol = "tcp"
    ports    = ["10250"]
  }
}

resource "google_compute_address" "koth" {
  for_each = local.vm_specs

  project      = google_project_service.compute[each.value.project_group].project
  name         = "msg-koth-${each.key}-ip"
  region       = "us-central1"
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"
}

resource "google_compute_disk" "boot" {
  for_each = local.vm_specs

  project = google_project_service.compute[each.value.project_group].project
  name    = "msg-koth-${each.key}-boot"
  zone    = "us-central1-a"
  type    = "pd-balanced"
  size    = each.value.boot_gib
  image   = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
  labels  = merge(local.labels, { challenge = each.key })

  lifecycle {
    prevent_destroy = true
    # An image-family update must not replace a disk containing competition state.
    ignore_changes = [image]
  }
}

resource "google_compute_disk" "dependency_artifacts" {
  count = contains(var.enabled_project_groups, "heavy") ? 1 : 0

  project = google_project_service.compute["heavy"].project
  name    = "msg-koth-dependency-hell-artifacts"
  zone    = "us-central1-a"
  type    = "pd-balanced"
  size    = 100
  labels  = merge(local.labels, { challenge = "dependency-hell" })

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_compute_instance" "koth" {
  for_each = local.vm_specs

  project        = var.project_ids[each.value.project_group]
  name           = "msg-${each.key}-1"
  zone           = "us-central1-a"
  machine_type   = each.value.machine_type
  desired_status = var.problem_vms_running ? "RUNNING" : "TERMINATED"

  lifecycle {
    ignore_changes = [desired_status]
  }
  allow_stopping_for_update = true
  deletion_protection       = true
  tags = concat(
    ["msg-koth"],
    each.key != "dependency-hell-runner" ? ["msg-koth-participant"] : [],
    each.value.health_port ? ["msg-koth-health"] : [],
    lookup(local.dependency_node_tags, each.key, []),
  )
  labels = merge(local.labels, { challenge = each.key })

  boot_disk {
    source      = google_compute_disk.boot[each.key].self_link
    auto_delete = false
  }

  dynamic "attached_disk" {
    for_each = each.key == "dependency-hell" ? { dependency-artifacts = google_compute_disk.dependency_artifacts[0].self_link } : {}

    content {
      source      = attached_disk.value
      device_name = attached_disk.key
      mode        = "READ_WRITE"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.koth[each.value.project_group].id

    access_config {
      nat_ip       = google_compute_address.koth[each.key].address
      network_tier = "PREMIUM"
    }
  }

  # Participant code must not inherit a project service account's Cloud permissions.
  metadata = {
    enable-oslogin          = "FALSE"
    block-project-ssh-keys  = "TRUE"
    serial-port-enable      = "FALSE"
    enable-guest-attributes = "TRUE"
    ssh-keys                = "sla:${local.sla_ssh_public_key}"
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    provisioning_model  = "STANDARD"
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
  }

  depends_on = [
    google_compute_firewall.participant,
    google_compute_firewall.health,
    google_compute_firewall.ssh,
    google_compute_firewall.dependency_k3s_api,
    google_compute_firewall.dependency_overlay,
    google_compute_firewall.dependency_kubelet,
  ]
}
