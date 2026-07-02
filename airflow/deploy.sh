#!/usr/bin/env bash
# Oracle Cloud ARM A1 VM bootstrap — Ubuntu 22.04 LTS (aarch64)
# Run as a non-root user with sudo privileges.
# Usage: bash deploy.sh
set -euo pipefail

REPO_URL="https://github.com/KacperChojnacki1337/cs2-market-data-platform.git"
APP_DIR="/opt/cs2-skin-vault"
AIRFLOW_DIR="${APP_DIR}/airflow"
GCP_KEY_PARAM="/steam-tracker/gcp-key"
AWS_REGION="eu-central-1"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
abort() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── 1. OS check ───────────────────────────────────────────────────────────────
[[ "$(uname -m)" == "aarch64" ]] || warn "Expected aarch64, got $(uname -m) — proceeding anyway"
. /etc/os-release 2>/dev/null || true
[[ "${ID:-}" == "ubuntu" ]] || abort "This script targets Ubuntu. Detected: ${PRETTY_NAME:-unknown}. Adapt for your distro."

# ── 2. System packages ────────────────────────────────────────────────────────
info "Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg git unzip python3-pip iptables

# ── 3. Docker CE ──────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    info "Installing Docker CE..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker "$USER"
    warn "Docker installed. You may need to log out and back in for group membership to take effect."
    warn "If 'docker ps' fails, run: newgrp docker"
else
    info "Docker already installed — skipping."
fi

# ── 4. AWS CLI v2 ─────────────────────────────────────────────────────────────
if ! command -v aws &>/dev/null; then
    info "Installing AWS CLI v2 (aarch64)..."
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp/awscli
    sudo /tmp/awscli/aws/install
    rm -rf /tmp/awscliv2.zip /tmp/awscli
else
    info "AWS CLI already installed — skipping."
fi

# ── 5. Clone / update repo ───────────────────────────────────────────────────
if [[ -d "${APP_DIR}/.git" ]]; then
    info "Repo already cloned — pulling latest develop..."
    git -C "${APP_DIR}" fetch origin
    git -C "${APP_DIR}" checkout develop
    git -C "${APP_DIR}" pull --ff-only origin develop
else
    info "Cloning repository..."
    sudo git clone "${REPO_URL}" "${APP_DIR}"
    sudo chown -R "$USER:$USER" "${APP_DIR}"
    git -C "${APP_DIR}" checkout develop
fi

cd "${AIRFLOW_DIR}"

# ── 6. Build .env from template ───────────────────────────────────────────────
if [[ ! -f .env ]]; then
    info "Creating .env from .env.example..."
    cp .env.example .env

    pip3 install -q cryptography 2>/dev/null || true
    FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    sed -i "s|<generate: python.*>|${FERNET_KEY}|" .env

    WEBSERVER_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s|<random string.*>|${WEBSERVER_SECRET}|" .env

    warn "--- ACTION REQUIRED ---"
    warn "Fill in the following fields in ${AIRFLOW_DIR}/.env before re-running this script:"
    warn "  POSTGRES_PASSWORD      — any strong password"
    warn "  AIRFLOW_ADMIN_EMAIL    — your email"
    warn "  AIRFLOW_ADMIN_PASSWORD — Airflow web UI password"
    warn "  AWS_ACCESS_KEY_ID      — IAM key with Lambda invoke + SSM read"
    warn "  AWS_SECRET_ACCESS_KEY  — corresponding secret"
    warn ""
    warn "Edit the file now:  nano ${AIRFLOW_DIR}/.env"
    warn "Then re-run this script."
    exit 0
fi

# ── 7. Validate required .env fields ──────────────────────────────────────────
required_fields=(
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY
    POSTGRES_PASSWORD
    AIRFLOW_ADMIN_EMAIL
    AIRFLOW_ADMIN_PASSWORD
)
missing=()
for field in "${required_fields[@]}"; do
    value=$(grep -E "^${field}=" .env | cut -d= -f2- | tr -d '"')
    [[ -z "$value" || "$value" == "changeme" ]] && missing+=("$field")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    abort "Missing or default values in .env: ${missing[*]}\nEdit ${AIRFLOW_DIR}/.env and re-run."
fi

# ── 8. Fetch GCP service-account key from SSM ─────────────────────────────────
GCP_KEY_DIR="${HOME}/.cs2-vault-secrets"
GCP_KEY_PATH="${GCP_KEY_DIR}/gcp-key.json"

if [[ ! -f "${GCP_KEY_PATH}" ]]; then
    info "Fetching GCP key from SSM (${GCP_KEY_PARAM})..."
    mkdir -p "${GCP_KEY_DIR}"
    chmod 700 "${GCP_KEY_DIR}"

    AWS_ACCESS_KEY_ID=$(grep "^AWS_ACCESS_KEY_ID=" .env | cut -d= -f2-)
    AWS_SECRET_ACCESS_KEY=$(grep "^AWS_SECRET_ACCESS_KEY=" .env | cut -d= -f2-)

    AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID}" \
    AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY}" \
    AWS_DEFAULT_REGION="${AWS_REGION}" \
    aws ssm get-parameter \
        --name "${GCP_KEY_PARAM}" \
        --with-decryption \
        --query "Parameter.Value" \
        --output text > "${GCP_KEY_PATH}"

    chmod 600 "${GCP_KEY_PATH}"
    info "GCP key saved to ${GCP_KEY_PATH}"
else
    info "GCP key already present — skipping SSM fetch."
fi

if grep -q "^GCP_KEY_HOST_PATH=\/path" .env; then
    sed -i "s|^GCP_KEY_HOST_PATH=.*|GCP_KEY_HOST_PATH=${GCP_KEY_PATH}|" .env
    info "GCP_KEY_HOST_PATH set to ${GCP_KEY_PATH}"
fi

# ── 9. Open Airflow port in OS firewall ───────────────────────────────────────
if ! sudo iptables -C INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null; then
    info "Opening port 8080 in iptables..."
    sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT
    warn "Remember to also open port 8080 in Oracle Cloud Security List (VCN → Subnet → Security List → Ingress Rules)."
fi

# ── 10. Pull images & start services ─────────────────────────────────────────
info "Pulling Docker images..."
docker compose pull --quiet

info "Running airflow-init (DB migrate + admin user create)..."
docker compose up airflow-init --exit-code-from airflow-init 2>/dev/null || true

info "Starting Airflow webserver and scheduler..."
docker compose up -d webserver scheduler

info "Waiting for webserver to be healthy (up to 2 min)..."
for i in $(seq 1 24); do
    if curl -sf http://localhost:8080/health 2>/dev/null | grep -q '"status": "healthy"'; then
        info "Airflow is up!"
        break
    fi
    [[ $i -eq 24 ]] && abort "Webserver did not become healthy after 2 minutes. Check logs: docker compose logs webserver"
    sleep 5
done

# ── 11. Summary ───────────────────────────────────────────────────────────────
PUBLIC_IP=$(curl -sf --max-time 3 http://ifconfig.me 2>/dev/null || echo "<your-vm-public-ip>")

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Airflow is running!${NC}"
echo -e "${GREEN}========================================${NC}"
echo "  Web UI:  http://${PUBLIC_IP}:8080"
echo "  Logs:    docker compose -f ${AIRFLOW_DIR}/docker-compose.yml logs -f"
echo "  Stop:    docker compose -f ${AIRFLOW_DIR}/docker-compose.yml down"
echo ""
echo "  Next steps:"
echo "  1. Open http://${PUBLIC_IP}:8080 — log in with credentials from .env"
echo "  2. Verify DAG 'cs2_daily_pipeline' appears and is unpaused"
echo "  3. Trigger a manual run to confirm Lambda invocations succeed"
echo "  4. After 2-3 days of stable runs → Phase 3 cutover (delete EventBridge rules)"
echo ""