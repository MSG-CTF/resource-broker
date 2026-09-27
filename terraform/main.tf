locals {
  # Compute Admin includes VM, disk, image, network, and firewall management.
  # It does not grant permission to change the project's IAM policy.
  compute_roles = toset([
    "roles/compute.admin",
  ])

  # The Broker creates/deletes assignments and reads per-VM compliance reports.
  # Keep these grants separate because OS policies can execute code as root.
  vm_manager_bootstrap_roles = toset([
    "roles/osconfig.osPolicyAssignmentAdmin",
    "roles/osconfig.osPolicyAssignmentReportViewer",
  ])

  project_roles = setunion(
    local.compute_roles,
    var.enable_vm_manager_bootstrap ? local.vm_manager_bootstrap_roles : toset([]),
  )

  project_role_bindings = {
    for binding in setproduct(var.target_project_ids, local.project_roles) :
    "${binding[0]} ${binding[1]}" => {
      project_id = binding[0]
      role       = binding[1]
    }
  }

  central_service_account_member = "serviceAccount:${var.central_service_account_email}"
}

# google_project_iam_member is additive: it preserves unrelated IAM members and
# roles already present in each target project.
resource "google_project_iam_member" "central_broker" {
  for_each = local.project_role_bindings

  project = each.value.project_id
  role    = each.value.role
  member  = local.central_service_account_member
}

# Project-wide actAs is intentionally opt-in because it can let the Broker run a
# VM as any service account owned by the target project. Prefer a per-service-
# account binding when the exact VM service account is known.
resource "google_project_iam_member" "central_broker_service_account_user" {
  for_each = var.grant_project_wide_service_account_user ? var.target_project_ids : toset([])

  project = each.value
  role    = "roles/iam.serviceAccountUser"
  member  = local.central_service_account_member
}
