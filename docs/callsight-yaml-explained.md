# What is `callsight.yaml`?

File: `deploy/aws/callsight.yaml`. This page explains it in plain words.

## 1. The short answer

`callsight.yaml` is a **shopping list and building plan for AWS**. It is a text file. It does not contain the
program, your calls, or any password. It only tells AWS: "create these parts, connect them like this".

You upload it once in the AWS page called **CloudFormation**. AWS reads the list and creates every part by
itself, in the right order. The group of parts it creates is called a **stack**. Our stack is named `callsight`.

Why a file, and not clicking each part by hand?

- About 35 parts are needed. By hand that is hundreds of clicks, and one wrong click breaks the system.
- The file can be checked before use (it passed an offline checker with no errors).
- To remove everything later you delete the stack, and AWS removes every part it created. Nothing is forgotten
  and left costing money.
- It touches only its own parts. Your other project in N. Virginia is not in this file and is not changed.

## 2. What it creates

### Step 1 (now): with "Create the GPU server? = no"

| Part | Plain meaning | Cost |
|---|---|---|
| Private network | A closed network just for CallSight, separate from everything else in your account | free |
| Main server | A small computer that is always on (`t3.medium`, Ubuntu, 100 GB encrypted disk). It will hold the calls, the database and the website | about $1.20–1.50 a day |
| Fixed internet address | One address that never changes. Needed for the website name and for the phone system's permit list | about $0.12 a day |
| Firewall rules | Who may talk to the main server (see section 4) | free |
| Setup key | The public half of a key made on this PC. Only this PC can log in to the server to install things | free |
| Daily disk copy | Every night AWS copies the main server's disk and keeps the last 7 copies | a few dollars a month |
| Cost alarm | Sends you an email when the month's bill passes $120 (only if you typed an email) | free |

The server is **empty** after this step: an operating system and nothing else. I install CallSight on it
afterwards, from this PC.

### Step 2 (later): with "Create the GPU server? = yes"

| Part | Plain meaning | Cost |
|---|---|---|
| GPU server | A computer with a graphics card (`g4dn.xlarge`). It turns speech into text and analyses the calls. It has no internet address | about $0.65 per hour **only while it is on** |
| Wake-up schedule | AWS starts the GPU server at :00 and :30, from 08:00 to 23:30 Riyadh time. The server switches itself off when no call is waiting | free |
| Permission for the schedule | Allows the schedule to do one thing only: start that one server | free |

This step needs AWS's approval of your request "Running On-Demand G and VT instances = 4" first.

## 3. The fields you see when you upload it

| Field on the page | What to put | What it does |
|---|---|---|
| Stack name | `callsight` | The name of the whole group of parts |
| Setup key (public part) | The line starting with `ssh-ed25519` that I gave you | Lets this PC log in to the servers. The secret half stays on this PC |
| Setup allowed from this internet address | This PC's address followed by `/32` | Only this one address can reach the login door (port 22). Empty = door closed |
| System image of the main server | Already filled | Which Ubuntu version is put on the main server. **Never change it later** |
| Email for the cost alarm | Your email, or empty | Where the cost warning goes. It counts the whole AWS account, also your other project |
| Create the GPU server? | `no` now, `yes` in step 2 | Adds the GPU server |
| System image of the GPU server | Already filled | The system for the GPU server (comes with the graphics driver). **Never change it later** |
| Zone of the GPU server | `1` | Which Frankfurt building the GPU server is in. All three offer it, so leave 1 |
| Wake the GPU server every 30 minutes? | `off` now, `on` at the very end | Switches the schedule on |
| Main / GPU instance type | Leave as they are | The size of each server |

The box **"I acknowledge that AWS CloudFormation might create IAM resources"** must be ticked. "IAM resources"
means permissions. The file creates two small ones: one lets AWS make the nightly disk copies, the other lets
the schedule start the GPU server. Nothing else.

## 4. Who can reach what (the firewall rules in the file)

- **Anyone on the internet:** only the website doors of the main server (ports 80 and 443). The website itself
  still asks for a user name and password.
- **This PC only:** the setup door (port 22), with the key.
- **The GPU server only:** the database, the job queue and the recordings folder (read-only) on the main server.
- **Nobody from the internet** can reach the GPU server. It has no internet address at all.

## 5. What the file does NOT do

- It does not install CallSight. I do that afterwards.
- It does not contain or copy any call, recording or password.
- It does not touch your other project, its servers or its settings.
- It does not connect to the phone system. That happens later, after the supplier opens access.
- It does not create the website name. Someone adds one record on your company's domain, pointing to the fixed
  address.

## 6. Changing it later

You change the stack with **Update stack** → **Use existing template** and change one field. Safe to change:
the internet address, the email, GPU yes/no, the zone (before the GPU server exists), the schedule on/off.

**Do not change** the two "System image" fields or the setup key after the servers exist. AWS would then
**replace the server with a new empty one** and delete its disk.

## 7. Removing everything (stops all cost)

1. EC2 → Instances → `callsight-main` → Actions → Instance settings → Change termination protection → off.
   (The file protects the main server against deletion by a wrong click, so this must be switched off first.)
2. CloudFormation → the stack `callsight` → **Delete**.
3. EC2 → Snapshots: delete the remaining disk copies.

## 8. Words used in the file

| Word in the file | Meaning |
|---|---|
| Parameters | The fields you fill in on the upload page |
| Resources | The parts AWS creates |
| Outputs | Values AWS shows after creating, for example **FixedAddress** |
| VPC, Subnet, RouteTable, InternetGateway | The private network and its roads |
| SecurityGroup | Firewall rules |
| EC2 Instance | A server |
| EIP (Elastic IP) | The fixed internet address |
| KeyPair | The setup key |
| DLM LifecyclePolicy | The nightly disk copies |
| Budget | The cost alarm |
| Scheduler Schedule | The 30-minute wake-up |
| IAM Role | A permission |

More detail for technical readers: `docs/aws.md`.
