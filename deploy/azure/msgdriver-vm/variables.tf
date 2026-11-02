variable "tenant_id" {
  description = "VM을 만들 Azure 구독의 Entra tenant ID"
  type        = string
}

variable "subscription_id" {
  description = "VM을 만들 Azure subscription ID"
  type        = string
}

variable "resource_group_name" {
  description = "이미 존재하는 Azure 리소스 그룹 이름"
  type        = string
}

variable "location" {
  description = "VM과 네트워크를 만들 Azure 리전"
  type        = string
  default     = "koreacentral"
}

variable "vm_name" {
  description = "Azure VM 이름. 특수화 이미지 안의 Windows 컴퓨터 이름은 원본에서 유지됨"
  type        = string
  default     = "msgdriver01"

  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9-]{0,14}$", var.vm_name))
    error_message = "vm_name은 영문자로 시작하는 15자 이내의 영문자, 숫자, 하이픈이어야 합니다."
  }
}

variable "source_image_version_id" {
  description = "소스 구독의 특수화 Windows v5 Gallery 이미지 버전 전체 ID"
  type        = string
}

variable "public_ip_enabled" {
  description = "VM에 고정 공인 IP를 만들지 여부"
  type        = bool
  default     = true
}
