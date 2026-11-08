#!/usr/bin/env bash
# Create the Azure VM for Settle: Ubuntu 24.04, B2als v2, in Southeast Asia (Singapore, next to
# the Neon database). Needs the Azure CLI, signed in with `az login`.
#
#   deploy/azure/create-vm.sh <dns-label> <ssh-public-key-file> <your-ip-for-ssh>
#
# The app is then reachable at https://<dns-label>.southeastasia.cloudapp.azure.com once
# deploy/azure/.env is in place and `docker compose up -d --build` has run (deployment.md).
set -euo pipefail

DNS_LABEL=$1
SSH_KEY=$2
ADMIN_IP=$3
RG=${RG:-settle-rg}
LOCATION=${LOCATION:-southeastasia}
VM=${VM:-settle-vm}
SIZE=${SIZE:-Standard_B2als_v2}  # 2 vCPU, 4 GB; B2s had no capacity in Singapore
HERE=$(cd "$(dirname "$0")" && pwd)
# The Azure CLI on Windows wants Windows paths, even when run from Git Bash.
if command -v cygpath >/dev/null; then
  HERE=$(cygpath -m "$HERE")
  SSH_KEY=$(cygpath -m "$SSH_KEY")
fi

az group create --name "$RG" --location "$LOCATION" --output none

az vm create \
  --resource-group "$RG" --name "$VM" --location "$LOCATION" \
  --image Ubuntu2404 --size "$SIZE" \
  --admin-username azureuser --ssh-key-values "$SSH_KEY" \
  --public-ip-sku Standard --public-ip-address-dns-name "$DNS_LABEL" \
  --custom-data "$HERE/cloud-init.yaml" \
  --nsg-rule NONE \
  --output table

# SSH only from the administrator's address; HTTP (for the certificate challenge and the
# redirect) and HTTPS from anywhere.
az network nsg rule create --resource-group "$RG" --nsg-name "${VM}NSG" \
  --name ssh-admin --priority 1000 --access Allow --protocol Tcp \
  --source-address-prefixes "$ADMIN_IP/32" --destination-port-ranges 22 --output none
az network nsg rule create --resource-group "$RG" --nsg-name "${VM}NSG" \
  --name web --priority 1010 --access Allow --protocol '*' \
  --source-address-prefixes Internet --destination-port-ranges 80 443 --output none

echo "VM ready: https://$DNS_LABEL.$LOCATION.cloudapp.azure.com (after the app is started)"
