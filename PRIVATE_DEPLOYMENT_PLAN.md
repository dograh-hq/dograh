# Voice AI on GCP — Private VPC Deployment Playbook

A step-by-step guide to deploying a voice AI stack (Dograh + Claude/Gemini + STT/TTS) entirely within a customer's GCP project, with no data leaving their VPC.

---

## Architecture overview

One VPC in the customer's GCP project. Inside it:

- A **GKE cluster** running Dograh (orchestration + Pipecat pipeline).
- A single **Private Service Connect (PSC) endpoint** to the `all-apis` bundle. This gives private access to *every* `*.googleapis.com` service in one shot — Vertex AI (Claude + Gemini), Cloud Speech-to-Text, Cloud Text-to-Speech, Cloud Storage, KMS, everything.
- Third-party TTS/STT (ElevenLabs or Deepgram) either as a Vertex Model Garden partner endpoint or as a Helm chart on the same GKE cluster.
- A **VPC Service Controls perimeter** around the project so leaked credentials can't exfiltrate data outside the perimeter.

```
┌─────────────────────────── Customer GCP Project ───────────────────────────┐
│  ┌─────────────────────── VPC: dograh-vpc ─────────────────────────────┐   │
│  │                                                                     │   │
│  │   ┌──────────────────┐         ┌──────────────────────────┐         │   │
│  │   │  GKE Cluster     │  ────▶  │  PSC Endpoint            │ ──▶ Vertex AI
│  │   │  (Dograh pods)   │         │  192.168.255.230         │ ──▶ Cloud STT
│  │   │  + optional      │         │  target: all-apis bundle │ ──▶ Cloud TTS
│  │   │  Deepgram pods   │         └──────────────────────────┘         │   │
│  │   └──────────────────┘                                              │   │
│  │                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
│   VPC Service Controls perimeter (deny egress outside perimeter)           │
└────────────────────────────────────────────────────────────────────────────┘
```

Traffic from GKE pods to Vertex AI, STT, and TTS resolves via private DNS to the PSC IP and stays entirely on Google's private backbone. The model inference itself still runs on Vertex's managed GPUs, but no audio or prompt content is accessible to Google or Anthropic.

---

## Phase 1 — VPC and PSC endpoint to Google APIs

This is the single piece of plumbing that gives Dograh private access to Vertex AI (Claude/Gemini), Cloud STT, and Cloud TTS.

### 1.1 Set variables and enable APIs

```bash
export PROJECT_ID=$(gcloud config get-value project)
export NETWORK=dograh-vpc
export REGION=us-east1
export PSC_IP=192.168.255.230   # any unused internal IP

gcloud services enable \
  compute.googleapis.com \
  aiplatform.googleapis.com \
  speech.googleapis.com \
  texttospeech.googleapis.com \
  dns.googleapis.com \
  servicedirectory.googleapis.com \
  container.googleapis.com
```

### 1.2 Create VPC and subnet

```bash
gcloud compute networks create $NETWORK \
  --subnet-mode=custom \
  --bgp-routing-mode=global \
  --mtu=1460

gcloud compute networks subnets create dograh-subnet \
  --network=$NETWORK \
  --range=10.0.0.0/20 \
  --region=$REGION \
  --enable-private-ip-google-access
```

The `--enable-private-ip-google-access` flag is required for VMs without external IPs to reach Google APIs through the PSC endpoint.

### 1.3 Reserve internal IP and create the PSC forwarding rule

```bash
gcloud compute addresses create dograh-psc-ip \
  --global \
  --purpose=PRIVATE_SERVICE_CONNECT \
  --addresses=$PSC_IP \
  --network=$NETWORK

gcloud compute forwarding-rules create dograh-psc-googleapis \
  --global \
  --network=$NETWORK \
  --address=dograh-psc-ip \
  --target-google-apis-bundle=all-apis
```

### 1.4 Wire up private DNS

```bash
gcloud dns managed-zones create googleapis-private \
  --description="Private DNS for googleapis.com" \
  --dns-name="googleapis.com." \
  --visibility="private" \
  --networks=$NETWORK

gcloud dns record-sets create "googleapis.com." \
  --zone=googleapis-private \
  --type=A \
  --ttl=300 \
  --rrdatas=$PSC_IP

gcloud dns record-sets create "*.googleapis.com." \
  --zone=googleapis-private \
  --type=CNAME \
  --ttl=300 \
  --rrdatas="googleapis.com."
```

### 1.5 Verify

From any VM inside the VPC:

```bash
dig aiplatform.googleapis.com +short
# Should return 192.168.255.230, not a Google public IP
```

---

## Phase 2 — Enable Claude and Gemini in Model Garden

These run on Vertex's managed infrastructure. The PSC endpoint from Phase 1 gives the private network path.

In the Cloud Console → **Vertex AI** → **Model Garden**:

1. Search "Claude", select Claude Opus 4.7 (or whichever tier the customer needs), click **Enable**, accept Anthropic's terms.
2. Search "Gemini", enable Gemini 3 Pro (typically enabled by default).

> **Note:** Model Garden requires a one-time terms acceptance per model per project, and cannot be automated via Terraform or gcloud. Document this as a manual onboarding step.

The Python clients then work over the PSC endpoint with no code changes:

```python
from anthropic import AnthropicVertex
from google import genai

claude = AnthropicVertex(region="us-east5", project_id=PROJECT_ID)
gemini = genai.Client(vertexai=True, location="us-east1", project=PROJECT_ID)
```

### Region selection

- For **data residency**, use regional endpoints (`us-east5`, `europe-west1`, etc.) instead of `global`. ~10% pricing premium, but requests are guaranteed to stay in that region.
- For maximum availability and feature freshness, use `global`.

---

## Phase 3 — Deploy Dograh on GKE in the same VPC

### 3.1 Create a private GKE cluster

```bash
gcloud container clusters create dograh-cluster \
  --region=$REGION \
  --network=$NETWORK \
  --subnetwork=dograh-subnet \
  --enable-private-nodes \
  --enable-private-endpoint \
  --master-ipv4-cidr=172.16.0.0/28 \
  --enable-ip-alias \
  --num-nodes=3 \
  --workload-pool=$PROJECT_ID.svc.id.goog

gcloud container clusters get-credentials dograh-cluster --region=$REGION
```

### 3.2 Install Dograh via Helm

```bash
helm install dograh ./charts/dograh -n dograh --create-namespace
```

Dograh pods inherit the VPC's DNS, so any call to `aiplatform.googleapis.com`, `speech.googleapis.com`, or `texttospeech.googleapis.com` automatically routes through the PSC endpoint.

### 3.3 Configure Workload Identity

This lets pods authenticate to Vertex AI without static service account keys.

```bash
# Kubernetes service account
kubectl create serviceaccount dograh-ksa -n dograh

# GCP service account
gcloud iam service-accounts create dograh-gsa

# Grant Vertex AI access
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:dograh-gsa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Bind KSA to GSA
gcloud iam service-accounts add-iam-policy-binding \
  dograh-gsa@$PROJECT_ID.iam.gserviceaccount.com \
  --member="serviceAccount:$PROJECT_ID.svc.id.goog[dograh/dograh-ksa]" \
  --role="roles/iam.workloadIdentityUser"

# Annotate KSA
kubectl annotate serviceaccount dograh-ksa -n dograh \
  iam.gke.io/gcp-service-account=dograh-gsa@$PROJECT_ID.iam.gserviceaccount.com
```

---

## Phase 4 — TTS and STT, pick your path

### Option A — All Google native (simplest)

Cloud Speech-to-Text v2 (Chirp 2) and Cloud Text-to-Speech (Chirp 3 HD voices) are already reachable through the PSC endpoint from Phase 1. **Zero additional setup.** Quality is solid for most CX use cases, though TTS expressiveness lags ElevenLabs and Cartesia.

### Option B — Deepgram self-hosted on GKE

Runs entirely in the customer's VPC, no egress to Deepgram cloud after licensing. Pure Helm chart.

**Prerequisites:** Engage Deepgram's enterprise sales to provision container image access and distribution credentials.

```bash
# GPU node pool (L4s are the sweet spot for Deepgram)
gcloud container node-pools create gpu-pool \
  --cluster=dograh-cluster \
  --region=$REGION \
  --machine-type=g2-standard-12 \
  --accelerator=type=nvidia-l4,count=1 \
  --num-nodes=2 \
  --node-locations=$REGION-b

# Optional: mirror Deepgram images from Quay to private Artifact Registry
gcloud artifacts repositories create deepgram \
  --repository-format=docker \
  --location=$REGION

# Install via Helm
helm repo add deepgram https://deepgram.github.io/self-hosted-resources
helm install dg-stt deepgram/deepgram-self-hosted \
  -f values.yaml \
  -n deepgram \
  --create-namespace
```

**Constraints:**
- NVIDIA GPUs only.
- Dedicated GPUs only — no MIG or fractional allocation.
- Linux x86-64 only.

### Option C — ElevenLabs via Vertex AI Model Garden

ElevenLabs deploys as a partner model on Vertex AI, accessed via the same `aiplatform.googleapis.com` PSC path you already have. Setup is sales-led, not self-serve through Marketplace — contact ElevenLabs enterprise, they provision the partner model in the customer's project, you call it via the standard Vertex Prediction API.

---

## Phase 5 — VPC Service Controls perimeter

This is the lock that turns "data flows over private network" into "data *cannot* leave the perimeter, even if a service account key is leaked."

```bash
# Get the org's access policy ID
gcloud access-context-manager policies list --organization=YOUR_ORG_ID

# Create the perimeter
gcloud access-context-manager perimeters create dograh-perimeter \
  --title="Dograh VPC-SC Perimeter" \
  --resources=projects/$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)') \
  --restricted-services=aiplatform.googleapis.com,speech.googleapis.com,texttospeech.googleapis.com,storage.googleapis.com,cloudkms.googleapis.com \
  --policy=YOUR_POLICY_ID
```

Any call to a restricted API from outside the perimeter (e.g., a developer laptop with leaked credentials) is denied at the API layer with `PERMISSION_DENIED` / `violationReason: VPC_SERVICE_CONTROLS`.

### CMEK (recommended)

Enable Customer-Managed Encryption Keys on the project for at-rest encryption with customer-controlled keys. This is the line-item that passes "data encrypted with our keys" in security reviews.

```bash
# Create a key ring and key
gcloud kms keyrings create dograh-keyring --location=$REGION
gcloud kms keys create dograh-key \
  --keyring=dograh-keyring \
  --location=$REGION \
  --purpose=encryption
```

Then attach the key to resources as needed (Vertex AI, Cloud Storage, GKE etcd, etc.).

---

## Phase 6 — Verification checklist

Run these from a Dograh pod inside the cluster:

```bash
# DNS resolves to PSC IP, not public Google IPs
dig aiplatform.googleapis.com +short
dig speech.googleapis.com +short
dig texttospeech.googleapis.com +short

# Vertex AI call succeeds over PSC
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-east1-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/us-east1/publishers/google/models/gemini-3-pro:generateContent" \
  -d '{"contents":[{"role":"user","parts":[{"text":"ping"}]}]}'

# Cloud STT reachable
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://speech.googleapis.com/v2/projects/$PROJECT_ID/locations/global/recognizers"

# From outside the perimeter (e.g., laptop with valid creds), the Vertex call
# should return PERMISSION_DENIED with violationReason: VPC_SERVICE_CONTROLS
```

---

## Two things to flag in the customer conversation

### 1. PSC endpoint ≠ model runs in their VPC

With this setup, audio and prompts travel from GKE pods to Vertex AI over Google's private backbone — they never touch the public internet, and neither Anthropic nor Google has access to the content. But the model inference itself runs on Vertex's managed GPUs in Google's infrastructure.

For roughly 95% of enterprise security reviews, this is acceptable and accurately described as "in our VPC." If the customer is a defense, sovereign-cloud, or air-gapped buyer who requires the GPU itself to be in their data center, skip this playbook entirely and use **Google Distributed Cloud air-gapped** with Gemini — a different motion (hardware-shipped, sales-led, Dell + NVIDIA Blackwell appliance).

### 2. Model Garden enablement is manual

Claude and other partner models require a one-time terms acceptance in the Cloud Console that cannot be automated via Terraform or gcloud. For multi-customer rollouts, document this as a manual step in onboarding.

---

## Quick reference: full command sequence

```bash
# 1. Variables
export PROJECT_ID=$(gcloud config get-value project)
export NETWORK=dograh-vpc
export REGION=us-east1
export PSC_IP=192.168.255.230

# 2. APIs
gcloud services enable compute.googleapis.com aiplatform.googleapis.com \
  speech.googleapis.com texttospeech.googleapis.com dns.googleapis.com \
  servicedirectory.googleapis.com container.googleapis.com

# 3. VPC
gcloud compute networks create $NETWORK --subnet-mode=custom --bgp-routing-mode=global
gcloud compute networks subnets create dograh-subnet --network=$NETWORK \
  --range=10.0.0.0/20 --region=$REGION --enable-private-ip-google-access

# 4. PSC endpoint
gcloud compute addresses create dograh-psc-ip --global \
  --purpose=PRIVATE_SERVICE_CONNECT --addresses=$PSC_IP --network=$NETWORK
gcloud compute forwarding-rules create dograh-psc-googleapis --global \
  --network=$NETWORK --address=dograh-psc-ip --target-google-apis-bundle=all-apis

# 5. Private DNS
gcloud dns managed-zones create googleapis-private --dns-name="googleapis.com." \
  --visibility="private" --networks=$NETWORK --description="Private DNS"
gcloud dns record-sets create "googleapis.com." --zone=googleapis-private \
  --type=A --ttl=300 --rrdatas=$PSC_IP
gcloud dns record-sets create "*.googleapis.com." --zone=googleapis-private \
  --type=CNAME --ttl=300 --rrdatas="googleapis.com."

# 6. GKE
gcloud container clusters create dograh-cluster --region=$REGION \
  --network=$NETWORK --subnetwork=dograh-subnet \
  --enable-private-nodes --enable-private-endpoint \
  --master-ipv4-cidr=172.16.0.0/28 --enable-ip-alias --num-nodes=3 \
  --workload-pool=$PROJECT_ID.svc.id.goog

# 7. Manual step: enable Claude + Gemini in Model Garden via console

# 8. VPC-SC perimeter (after getting org policy ID)
gcloud access-context-manager perimeters create dograh-perimeter \
  --title="Dograh VPC-SC Perimeter" \
  --resources=projects/$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)') \
  --restricted-services=aiplatform.googleapis.com,speech.googleapis.com,texttospeech.googleapis.com,storage.googleapis.com,cloudkms.googleapis.com \
  --policy=YOUR_POLICY_ID
```