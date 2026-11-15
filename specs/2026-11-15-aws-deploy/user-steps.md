# The user's AWS steps, click by click

For the user (does not know AWS). One step at a time: give only the current step, wait for "done", then the next.
If a screen looks different, ask for a screenshot. Tick a step here when the user confirms it.

Current step: **A5** (supplier) and the "save" answer; A3 sent, waiting for AWS approval

## A2: location (free)
- [x] done 2026-11-15: Bahrain was enabled but its website does not answer. The user chose **Europe (Frankfurt)**,
  which is on by default. The numbered clicks below were for Bahrain and are no longer needed.
1. Open https://console.aws.amazon.com and sign in with the AWS email and password.
2. Top right corner: click the account name.
3. In the small menu, click **Account**.
4. Scroll down to the heading **AWS Regions** (a table with a list of places).
5. Find the line **Middle East (Bahrain)**.
6. If it says **Disabled**: tick the small box beside it, click **Enable** above the table, then **Enable region**.
7. It shows **Enabling**, and after a few minutes **Enabled**. No need to wait on the page.
   If it already says **Enabled**, nothing to do.

## A3: ask AWS to allow the graphics-card server (free; approval can take up to two days)
- [x] request sent (2026-11-15, Frankfurt, value 4, status "Case Opened")
- [ ] approved by AWS (email from AWS, or the value shows 4)
1. Top right, next to the account name: open the location list and choose **Europe (Frankfurt)**.
2. In the search box at the top of the page, type **Service Quotas** and open it.
3. On the left, click **AWS services**.
4. Search for **Amazon Elastic Compute Cloud (Amazon EC2)** and open it.
5. In the search box of that page, type **Running On-Demand G and VT instances** and open it.
6. Click **Request increase at account level**.
7. Type **4** as the new value and click **Request**.

## A3b: ask AWS for three facts (free, 2 minutes; needs A2 = Enabled)
- [x] answer sent 2026-11-15: Ubuntu `ami-04bbcae8eccec8bce`, GPU image `ami-0b7e7967a14fff93a`, `g4dn.xlarge` offered in
  eu-central-1a/b/c (so zone 1 is fine). Both ids are now the default values in `callsight.yaml`.
1. Top bar of the AWS website, right of the search box: click the small icon that looks like a window with
   `>_` in it (**CloudShell**). A black window opens at the bottom. Wait until a line ending with `$` shows.
2. Copy the whole line below, paste it into the black window (right-click → Paste), press Enter:

   `R=eu-central-1; aws ssm get-parameter --region $R --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text; aws ssm get-parameter --region $R --name /aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id --query Parameter.Value --output text; aws ec2 describe-instance-type-offerings --region $R --location-type availability-zone --filters Name=instance-type,Values=g4dn.xlarge --query 'InstanceTypeOfferings[].Location' --output text; aws ec2 describe-availability-zones --region $R --query 'AvailabilityZones[].ZoneName' --output text`

3. It prints four lines (two start with `ami-`, two with `eu-central-1`). Send them (copy or screenshot).
   It only reads public information; it creates nothing and costs nothing.

Claude then writes the two `ami-` values and the zone into the fields of step A4.

## A4: create the main server by uploading one file (costs start here, about $1.80 a day)
- [x] done 2026-11-15: stack `callsight` in Frankfurt, created with "Disable rollback". FixedAddress **63.184.90.120**,
  MainPrivateAddress 10.20.1.10. SSH allowed from 143.92.143.60/32 (the PC that day); key `~/.ssh/callsight_aws`.
Before this step Claude gives the values for the fields (setup key, this PC's internet address, the image id).
1. Top right: the location must say **Europe (Frankfurt)**.
2. Search box at the top: type **CloudFormation**, open it.
3. Click **Create stack** → **With new resources (standard)**.
4. Choose **Choose an existing template** → **Upload a template file** → **Choose file** → pick
   `D:\claude\data_analsis_call\deploy\aws\callsight.yaml` → **Next**.
5. **Stack name:** type `callsight`.
6. Fill the fields of group 1 with the values Claude gave. Leave group 2 as it is (**Create the GPU server? = no**)
   and group 3 as it is. → **Next**.
7. Next page: scroll to the bottom, tick the box **I acknowledge that AWS CloudFormation might create IAM
   resources** → **Next** → **Submit**.
8. Wait about 5 minutes until the status says **CREATE_COMPLETE** (the round arrow refreshes the page).
   If it says **ROLLBACK** anything: send a screenshot of the tab **Events**.
9. Open the tab **Outputs** and send the value of **FixedAddress**.

## A4c: add the GPU server (only after A3 is approved; costs about $0.65 per hour while it is on)
- [ ] done
1. CloudFormation → click the stack **callsight** → **Update stack** → **Make a direct update**.
2. **Use existing template** → **Next**. (If the file changed since A4, Claude says so: then choose
   **Replace existing template** and upload the file again.)
3. Change only: **Create the GPU server?** → `yes`, and paste the value Claude gave into **System image of the GPU
   server**. Do not touch the other fields. → **Next**.
4. Bottom of the next page: tick the **I acknowledge…** box → **Next** → **Submit**.
5. Wait for **UPDATE_COMPLETE**, then tell Claude at once: the GPU server is running and costs money until Claude
   has set it up and it powers itself off.

## A6: switch on the 30-minute wake-up (last step)
- [ ] done
Same as A4c, steps 1–4, but change only **Wake the GPU server every 30 minutes?** → `on`.

## A4b: point the website name to the server
- [x] done 2026-11-15: **calls.mohanadyehia.com** → 63.184.90.120 (type A, "DNS only"). The domain is the user's own
  (portfolio site), registered and managed at Cloudflare, valid until 2027-09-06. The company has no domain; the user
  was told the call system's address depends on this personal domain being renewed. Checked from the PC: the name
  answers with the server's address.

## A5: phone company opens API Access
- [ ] asked
- [ ] done
Message to send: "Please enable **API Access** on our Yeastar FQDN (`sstli.ras.yeastar.com`) and permit one IP
address only: 63.184.90.120."
