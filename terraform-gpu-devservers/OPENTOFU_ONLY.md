# ⚠️ CRITICAL: This Project Uses OpenTofu ONLY

## 🚨 NEVER Use `terraform` Command 🚨

**This project exclusively uses OpenTofu (`tofu`). Using `terraform` will corrupt the infrastructure state and cause deployment failures.**

## Why OpenTofu?

- **State format compatibility**: OpenTofu and Terraform diverged at version 1.6.x
- **Licensing**: OpenTofu is truly open source (MPL 2.0), Terraform changed to BSL
- **Community driven**: OpenTofu is community-maintained and vendor-neutral
- **Feature parity**: OpenTofu maintains compatibility with Terraform 1.6.x and beyond

## The Risk

Using `terraform` commands on this codebase will:
- ❌ Corrupt the state file (OpenTofu and Terraform have incompatible state formats)
- ❌ Cause resource drift and unpredictable behavior
- ❌ Break deployments for everyone on the team
- ❌ Require manual state file recovery or infrastructure rebuild

## Commands

### ✅ CORRECT - Use OpenTofu

```bash
# Initialize
tofu init

# Plan changes
tofu plan

# Apply changes
tofu apply

# Destroy resources
tofu destroy

# Show state
tofu state list

# Output values
tofu output
```

### ❌ WRONG - Never Use Terraform

```bash
# ⛔ DON'T RUN THESE COMMANDS
terraform init
terraform plan
terraform apply
terraform destroy
terraform state list
terraform output
```

## Installation

If you don't have OpenTofu installed:

```bash
# macOS (Homebrew)
brew install opentofu

# Linux
# See: https://opentofu.org/docs/intro/install/

# Verify installation
tofu version
```

## Safety Checks

### Before Running ANY Command

1. **Verify you're using OpenTofu:**
   ```bash
   which tofu
   # Should output: /opt/homebrew/bin/tofu (or similar)
   ```

2. **Check for dangerous aliases:**
   ```bash
   alias | grep terraform
   # Should output nothing or show terraform as a separate command
   ```

3. **Ensure terraform is NOT in your PATH or is a different binary:**
   ```bash
   terraform version 2>&1 | grep -i "not found" && echo "✅ Safe - terraform not found"
   ```

### If You Accidentally Ran `terraform`

**STOP IMMEDIATELY** and:

1. **Do NOT commit any state file changes**
   ```bash
   git status
   git restore terraform.tfstate*
   ```

2. **Notify the team** - State file may be corrupted

3. **Restore from backup** or re-init with OpenTofu:
   ```bash
   rm -rf .terraform/
   tofu init
   ```

4. **Verify state is correct:**
   ```bash
   tofu plan
   # Should show no changes if state is good
   ```

## All Scripts Updated

Every script in this repository uses `tofu`:
- ✅ `recreate-database.sh`
- ✅ `deploy-timeout-fix.sh`
- ✅ `fix-disk-size-column.sh`
- ✅ All documentation references

## Team Guidelines

1. **Never alias terraform to tofu** - This hides which tool you're using
2. **Always use explicit `tofu` command** - Makes it clear what you're running
3. **Review scripts before running** - Ensure they use `tofu`, not `terraform`
4. **Update documentation** - If you add new scripts, use `tofu` commands

## Documentation References

See these files for more context:
- [`reservation-processor-service/README.md`](reservation-processor-service/README.md) - Deployment guidelines
- [`DATABASE_RECREATION_GUIDE.md`](DATABASE_RECREATION_GUIDE.md) - Database management
- All `.sh` scripts in the repository

## Quick Reference

| Task | Command |
|------|---------|
| Deploy all infrastructure | `tofu apply` |
| Deploy specific resource | `tofu apply -target=<resource>` |
| Preview changes | `tofu plan` |
| Get output values | `tofu output <name>` |
| Show resources | `tofu state list` |
| Destroy everything | `tofu destroy` |

## Questions?

If you need to run any infrastructure commands and you're not sure:

1. ✅ Use `tofu` - It's always safe
2. ❌ Don't use `terraform` - It will break things
3. 💬 Ask the team if unsure - Better safe than sorry

---

**Remember: `tofu` good, `terraform` bad (for this project)**

