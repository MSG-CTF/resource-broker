resource "google_project_iam_member" "broker_compute_viewer" {
  for_each = toset(["heavy", "pixel_grand"])

  project = var.project_ids[each.key]
  role    = "roles/compute.viewer"
  member  = "serviceAccount:${var.central_service_account_email}"
}
