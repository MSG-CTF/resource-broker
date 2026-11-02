resource "google_project_iam_member" "broker_compute_viewer" {
  for_each = toset(["heavy", "pixel_grand"])

  project = var.project_ids[each.key]
  role    = "roles/compute.viewer"
  member  = "serviceAccount:${var.central_service_account_email}"
}

resource "google_project_iam_custom_role" "broker_vm_power" {
  for_each = toset(["heavy", "pixel_grand"])

  project     = var.project_ids[each.key]
  role_id     = "msgBrokerVmPower"
  title       = "MSG Broker VM Power"
  permissions = ["compute.instances.start", "compute.instances.stop"]
}

resource "google_project_iam_member" "broker_vm_power" {
  for_each = toset(["heavy", "pixel_grand"])

  project = var.project_ids[each.key]
  role    = google_project_iam_custom_role.broker_vm_power[each.key].name
  member  = "serviceAccount:${var.central_service_account_email}"
}
