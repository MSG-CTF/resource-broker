output "central_service_account_member" {
  description = "IAM member string granted access to the target projects."
  value       = local.central_service_account_member
}

output "target_project_ids" {
  description = "Projects managed by this Terraform state."
  value       = sort(tolist(var.target_project_ids))
}

output "granted_roles_by_project" {
  description = "Project-level roles managed by this Terraform state."
  value = {
    for project_id in var.target_project_ids : project_id => sort(concat(
      tolist(local.project_roles),
      var.grant_project_wide_service_account_user ? ["roles/iam.serviceAccountUser"] : [],
    ))
  }
}
