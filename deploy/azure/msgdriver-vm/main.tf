terraform {
  required_version = ">= 1.5.0"

  backend "local" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 4.50.0"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id                 = var.subscription_id
  tenant_id                       = var.tenant_id
  resource_provider_registrations = "none"
}

data "azurerm_resource_group" "target" {
  name = var.resource_group_name
}

resource "azurerm_virtual_network" "msgdriver" {
  name                = "${var.vm_name}-vnet"
  location            = var.location
  resource_group_name = data.azurerm_resource_group.target.name
  address_space       = ["172.16.0.0/16"]
}

resource "azurerm_subnet" "msgdriver" {
  name                 = "${var.vm_name}-subnet"
  resource_group_name  = data.azurerm_resource_group.target.name
  virtual_network_name = azurerm_virtual_network.msgdriver.name
  address_prefixes     = ["172.16.0.0/24"]
}

resource "azurerm_network_security_group" "msgdriver" {
  name                = "${var.vm_name}-nsg"
  location            = var.location
  resource_group_name = data.azurerm_resource_group.target.name
}

resource "azurerm_public_ip" "msgdriver" {
  count               = var.public_ip_enabled ? 1 : 0
  name                = "${var.vm_name}-ip"
  location            = var.location
  resource_group_name = data.azurerm_resource_group.target.name
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_network_interface" "msgdriver" {
  name                = "${var.vm_name}-nic"
  location            = var.location
  resource_group_name = data.azurerm_resource_group.target.name

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.msgdriver.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = var.public_ip_enabled ? azurerm_public_ip.msgdriver[0].id : null
  }
}

resource "azurerm_network_interface_security_group_association" "msgdriver" {
  network_interface_id      = azurerm_network_interface.msgdriver.id
  network_security_group_id = azurerm_network_security_group.msgdriver.id
}

resource "azurerm_managed_disk" "msgdriver_os" {
  name                       = "${var.vm_name}-os"
  location                   = var.location
  resource_group_name        = data.azurerm_resource_group.target.name
  storage_account_type       = "Standard_LRS"
  create_option              = "FromImage"
  gallery_image_reference_id = var.source_image_version_id
  os_type                    = "Windows"
  disk_size_gb               = 127
  hyper_v_generation         = "V2"
  trusted_launch_enabled     = true
}

resource "azurerm_windows_virtual_machine" "msgdriver" {
  name                  = var.vm_name
  location              = var.location
  resource_group_name   = data.azurerm_resource_group.target.name
  size                  = "Standard_B2als_v2"
  network_interface_ids = [azurerm_network_interface.msgdriver.id]
  os_managed_disk_id    = azurerm_managed_disk.msgdriver_os.id
  secure_boot_enabled   = false
  vtpm_enabled          = true

  os_disk {
    caching = "ReadWrite"
  }

  depends_on = [azurerm_network_interface_security_group_association.msgdriver]
}

output "vm_id" {
  value = azurerm_windows_virtual_machine.msgdriver.id
}

output "public_ip" {
  value = var.public_ip_enabled ? azurerm_public_ip.msgdriver[0].ip_address : null
}
