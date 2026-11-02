output "vms" {
  description = "VM inventory. Participant URLs respond after the challenge services have been installed."
  value = {
    for challenge, vm in google_compute_instance.koth : challenge => {
      project_id      = vm.project
      name            = vm.name
      zone            = vm.zone
      machine_type    = vm.machine_type
      status          = vm.current_status
      public_ip       = google_compute_address.koth[challenge].address
      private_ip      = vm.network_interface[0].network_ip
      boot_disk_gib   = local.vm_specs[challenge].boot_gib
      participant_url = challenge != "dependency-hell-runner" ? "http://${google_compute_address.koth[challenge].address}:8080" : null
      ssh_username    = "sla"
      ssh_command     = "ssh -i ~/.ssh/sla_to_control sla@${google_compute_address.koth[challenge].address}"
    }
  }
}

output "dependency_artifacts_disk" {
  description = "Unformatted, attached 100 GiB block disk; this does not provision an RWX PVC or mount a filesystem."
  value = contains(var.enabled_project_groups, "heavy") ? {
    name       = google_compute_disk.dependency_artifacts[0].name
    size_gib   = google_compute_disk.dependency_artifacts[0].size
    guest_path = "/dev/disk/by-id/google-dependency-artifacts"
  } : null
}
