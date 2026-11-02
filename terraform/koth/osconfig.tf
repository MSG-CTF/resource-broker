# Enable VM Manager for the three shared-project VMs.
resource "google_project_service" "osconfig" {
  project            = var.project_ids.shared
  service            = "osconfig.googleapis.com"
  disable_on_destroy = false
}

resource "google_compute_project_metadata_item" "enable_osconfig" {
  project = var.project_ids.shared
  key     = "enable-osconfig"
  value   = "TRUE"

  depends_on = [google_project_service.osconfig]
}
