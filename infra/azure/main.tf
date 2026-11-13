resource "azurerm_resource_group" "bench" {
  name     = var.resource_group
  location = var.location
}
locals {
  hosts = merge(
    { for i in range(var.node_count) : "engine-${i + 1}" => { size = var.node_size, zone = element(var.node_zones, i), node = true } },
    { for i in range(2) : "loadgen-${i + 1}" => { size = var.loadgen_size, zone = "1", node = false } }
  )
}
resource "azurerm_virtual_network" "bench" {
  name                = "benchmark"
  location            = var.location
  resource_group_name = azurerm_resource_group.bench.name
  address_space       = ["10.62.0.0/16"]
}
resource "azurerm_subnet" "bench" {
  name                 = "benchmark"
  resource_group_name  = azurerm_resource_group.bench.name
  virtual_network_name = azurerm_virtual_network.bench.name
  address_prefixes     = ["10.62.1.0/24"]
}
resource "azurerm_network_security_group" "bench" {
  name                = "benchmark"
  location            = var.location
  resource_group_name = azurerm_resource_group.bench.name
  security_rule {
    name                       = "ssh"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefixes    = var.ssh_source_cidrs
    destination_address_prefix = "*"
  }
}
resource "azurerm_subnet_network_security_group_association" "bench" {
  subnet_id                 = azurerm_subnet.bench.id
  network_security_group_id = azurerm_network_security_group.bench.id
}
resource "azurerm_public_ip" "bench" {
  for_each            = local.hosts
  name                = "${each.key}-ip"
  location            = var.location
  resource_group_name = azurerm_resource_group.bench.name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = [each.value.zone]
}
resource "azurerm_network_interface" "bench" {
  for_each                       = local.hosts
  name                           = each.key
  location                       = var.location
  resource_group_name            = azurerm_resource_group.bench.name
  accelerated_networking_enabled = true
  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.bench.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.bench[each.key].id
  }
}
resource "azurerm_linux_virtual_machine" "bench" {
  for_each              = local.hosts
  name                  = each.key
  location              = var.location
  resource_group_name   = azurerm_resource_group.bench.name
  size                  = each.value.size
  zone                  = each.value.zone
  admin_username        = var.admin_user
  network_interface_ids = [azurerm_network_interface.bench[each.key].id]
  admin_ssh_key {
    username   = var.admin_user
    public_key = file(pathexpand(var.ssh_public_key_file))
  }
  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
    disk_size_gb         = 64
  }
  source_image_reference {
    publisher = "Debian"
    offer     = "debian-13"
    sku       = "13-gen2"
    version   = var.image_version
  }
  custom_data = base64encode("#cloud-config\npackage_update: true\npackages: [rsync, curl, jq, xz-utils, python3]\n")
}
resource "azurerm_managed_disk" "data" {
  for_each             = { for k, v in local.hosts : k => v if v.node }
  name                 = "${each.key}-data"
  location             = var.location
  resource_group_name  = azurerm_resource_group.bench.name
  storage_account_type = "PremiumV2_LRS"
  create_option        = "Empty"
  disk_size_gb         = 1024
  disk_iops_read_write = 20000
  disk_mbps_read_write = 900
  zone                 = each.value.zone
}
resource "azurerm_virtual_machine_data_disk_attachment" "data" {
  for_each           = azurerm_managed_disk.data
  managed_disk_id    = each.value.id
  virtual_machine_id = azurerm_linux_virtual_machine.bench[each.key].id
  lun                = 0
  caching            = "None"
}
output "inventory" {
  value = {
    resource_group = azurerm_resource_group.bench.name
    location       = var.location
    admin_user     = var.admin_user
    nodes          = [for k, v in local.hosts : { name = k, public_ip = azurerm_public_ip.bench[k].ip_address, private_ip = azurerm_network_interface.bench[k].private_ip_address, size = v.size } if v.node]
    loadgens       = [for k, v in local.hosts : { name = k, public_ip = azurerm_public_ip.bench[k].ip_address, private_ip = azurerm_network_interface.bench[k].private_ip_address, size = v.size } if !v.node]
  }
}
