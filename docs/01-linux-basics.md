# 01 · Linux basics — connect to the VM and find your way around

> Goal: log in to the VM, run a command, edit a file, and copy a file to it.
> That's everything you need for the rest of the guide.

If you already use Linux, skim the **"Transfer a file to the VM"** section (you'll
need it in step 04) and move on.

---

## 1. What you need

From whoever gave you the VM, get three things:

- **IP address** of the VM, e.g. `10.12.0.5`
- **Username**, e.g. `msei`
- **Password** *or* an **SSH key file** (a file like `id_ed25519`)

Write them down. In the examples below replace `msei@10.12.0.5` with your own
`username@ip`.

---

## 2. Connect with SSH

SSH lets you type commands on the VM from your own computer.

### On Windows
Open **PowerShell** (press `Start`, type `PowerShell`, hit Enter) and run:

```powershell
ssh msei@10.12.0.5
```

### On macOS / Linux
Open **Terminal** and run the same line:

```bash
ssh msei@10.12.0.5
```

The first time, it asks *"Are you sure you want to continue connecting?"* — type
`yes` and press Enter. Then type your password (the screen shows nothing while you
type — that's normal) and press Enter.

You're in when the prompt changes to something like:

```
msei@msei-vm:~$
```

That `$` is where you type commands. The `~` means you are in your **home folder**.

> **Using a key file instead of a password?**
> `ssh -i C:\path\to\id_ed25519 msei@10.12.0.5`

> **To leave the VM** at any time, type `exit` and press Enter.

---

## 3. Ten commands that get you through everything

Type a command, press Enter. That's it.

| Command | What it does |
|---------|--------------|
| `pwd` | Print the folder you're in ("where am I?"). |
| `ls` | List files in this folder. |
| `ls -lh` | List files with sizes and dates. |
| `cd foldername` | Go **into** a folder. |
| `cd ..` | Go **up** one folder. |
| `cd ~` | Go back to your home folder. |
| `mkdir name` | Make a new folder. |
| `cat file` | Print a file's contents to the screen. |
| `nano file` | Open a file in a simple editor (see below). |
| `clear` | Clear the screen. |

> **Tip:** press the **Tab** key to auto-complete file and folder names. Press the
> **Up arrow** to repeat a previous command.

---

## 4. `sudo` — running commands as administrator

Installing software needs administrator rights. You do that by putting `sudo` in
front of a command:

```bash
sudo apt update
```

It will ask for your password the first time. `sudo` = "do this as the superuser".
Only use it when the guide tells you to.

---

## 5. Editing a file with `nano`

We use `nano` because it's the friendliest editor. To open (or create) a file:

```bash
nano .env
```

- Type normally to edit.
- **Save:** press `Ctrl` + `O`, then `Enter`.
- **Exit:** press `Ctrl` + `X`.
- The bottom of the screen always reminds you (`^O` means `Ctrl+O`).

That's all the `nano` you need.

---

## 6. Transfer a file to the VM (you'll need this in step 04)

The snapshot bundle from the ingest repo lives on **your** computer. To copy it
onto the VM, use `scp` ("secure copy"). Run this **on your own computer** (a fresh
PowerShell/Terminal that is *not* logged into the VM):

```bash
scp "C:\Users\you\Downloads\colleague_xxx_qdrant_20260601.tar.gz" msei@10.12.0.5:~/
```

Breaking that down:
- `scp` — the copy command.
- `"...tar.gz"` — the file on your computer (keep the quotes if the path has spaces).
- `msei@10.12.0.5:~/` — copy it to your home folder (`~/`) on the VM.

When it finishes, log back into the VM (`ssh msei@10.12.0.5`) and run `ls -lh` —
you'll see the file there.

> **macOS/Linux** path example:
> `scp ~/Downloads/colleague_xxx_qdrant_20260601.tar.gz msei@10.12.0.5:~/`

> **Big files / unreliable connection?** Use `rsync` instead — it can resume:
> `rsync -avP file.tar.gz msei@10.12.0.5:~/`

---

## 7. Find the VM's own IP address (you'll need it in step 06/07)

Users will point OpenCode at the VM's IP. While logged into the VM, run:

```bash
hostname -I
```

It prints one or more addresses, e.g. `10.12.0.5 172.17.0.1`. The first one on
your group's network (here `10.12.0.5`) is the address users will use. Write it
down.

---

## You're ready

You can now: log in, move around, edit files, and copy files to the VM. That's
the entire Linux skill set this project needs.

⬅️ Back: [00 · Overview](00-overview.md)  ·  ➡️ Next: [02 · Install Docker](02-install-docker.md)
