variable "resource_group" { default = "rivet-celld-benchmark" }
variable "location" { default = "eastus2" }
variable "admin_user" { default = "rivet" }
variable "ssh_public_key_file" { default = "~/.ssh/id_ed25519.pub" }
variable "ssh_source_cidrs" {
  type        = list(string)
  description = "Your egress IP CIDRs allowed to connect over SSH."
}
variable "node_size" { default = "Standard_D16as_v7" }
variable "node_count" {
  type = number
  default = 6
  validation {
    condition = contains([6, 12, 24], var.node_count)
    error_message = "Use six, twelve or twenty-four platform nodes for this benchmark."
  }
}
variable "loadgen_size" { default = "Standard_D8as_v7" }
variable "node_zones" { default = ["1", "1", "1", "1", "1", "1"] }
variable "image_version" { default = "0.20261017.2634" }
