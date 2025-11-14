# Security Audit Report

- **Date:** 2025-11-14
- **Audited By:** GitHub Copilot (automated static review)
- **Repository:** `CodeOwnersScripts`
- **Scope:** `scripts/run_ui.py`, `codeowners_tools/*`, supporting modules

## Methodology

Manual static analysis of the Python source with a focus on request handling, file-system access, subprocess usage, and network interactions. No automated scanning or dynamic testing was executed in this pass.

## Findings

### ⚠️ High: Unauthenticated HTTP UI Exposes Audit Controls
- **Location:** `scripts/run_ui.py` (`RunServer`, `AuditDashboardHandler`)
- **Issue:** The dashboard binds to an arbitrary host/port without any authentication, authorization, or TLS. If the process is started with `--host 0.0.0.0` (or behind port-forwarding), any network peer can trigger audits, inspect results, and download regenerated CODEOWNERS files.
- **Impact:** Unauthorized users can enumerate repositories, inspect audit outputs, or exfiltrate CODEOWNERS data from systems where the service is reachable.
- **Recommendations:**
  - Require explicit authentication (API token, OAuth, or reverse-proxy auth) before serving audit data.
  - Default-bind strictly to localhost and document the risk of widening the bind address.
  - Terminate TLS at a trusted reverse proxy or embed HTTPS when exposed beyond the local machine.

### ⚠️ High: Arbitrary Local Repository Access Leads to Data Disclosure
- **Location:** `_run_audit_from_form` in `scripts/run_ui.py`
- **Issue:** User input for `repo_root` is resolved verbatim, allowing remote clients to point the audit at any Git working tree accessible to the process account.
- **Impact:** A remote attacker can read audit summaries and CODEOWNERS entries for private repositories on the host machine. If the service runs with elevated privileges, this can leak highly sensitive information.
- **Recommendations:**
  - Enforce an allow-list of repository roots or disable direct path inputs for remote users.
  - Run the service under a dedicated, least-privileged account with access only to intended repositories.
  - Provide server-side configuration for permissible repositories rather than trusting client input.

### ⚠️ Medium: Server-Side Request Forgery via Unrestricted `git clone`
- **Location:** `codeowners_tools/remote.py::_clone_repository`
- **Issue:** The dashboard accepts arbitrary `repo_url` values and executes `git clone` against them without validation.
- **Impact:** An attacker can coerce the service into reaching internal Git endpoints or arbitrary URLs, potentially abusing credentials on the host (e.g., SSH agent, git credential store). Although hooks are not executed on clone, this still enables SSRF-style access.
- **Recommendations:**
  - Restrict acceptable URL schemes (e.g., limit to `https://` domains on an allow-list).
  - Optionally disable remote cloning in production builds or require a server-side configuration toggle.
  - Execute cloning in a network-restricted sandbox when possible.

### ⚠️ Medium: Unbounded Resource Consumption During Git Operations
- **Location:** `remote.py::_clone_repository`, `git_activity.py::build_activity_index`
- **Issue:** Requests can trigger cloning of arbitrarily large repositories and run `git log` over unbounded history.
- **Impact:** A malicious client can exhaust disk space, memory, or CPU, leading to denial-of-service.
- **Recommendations:**
  - Impose size/time limits on clones (e.g., shallow clone, depth-limited history).
  - Add request throttling and concurrent job limits.
  - Validate repository sizes and reject overly large or long-running operations.

### ⚠️ Low: Trusting Client `Content-Length` Enables DoS
- **Location:** `AuditDashboardHandler._read_form`
- **Issue:** The handler reads the entire request body based solely on `Content-Length` without size checks.
- **Impact:** An attacker can advertise an enormous length, causing the process to allocate excessive memory or hang reading from the socket.
- **Recommendations:**
  - Enforce a maximum request size and reject larger payloads early.
  - Consider streaming form parsing or leveraging higher-level frameworks with built-in limits.

## Additional Observations

- Error messages bubble up to the UI by stringifying exceptions; ensure sensitive paths or tokens are not leaked in production deployments.
- The download endpoint relies on a UUID token stored in memory. While unpredictable, tokens never expire. Consider revoking tokens after download or on a timer.

## Suggested Hardening Steps

1. Place the dashboard behind an authenticated reverse proxy (e.g., nginx, Traefik) with mandatory TLS.
2. Introduce server-side configuration for permissible repositories/URLs and reject any client-provided values outside those sets.
3. Implement rate limiting and per-request timeouts for git operations.
4. Add input validation and size limits for all form fields.
5. Run the service in an isolated container or VM with minimal filesystem and network privileges.

## Files Reviewed

- `scripts/run_ui.py`
- `codeowners_tools/remote.py`
- `codeowners_tools/audit.py`
- `codeowners_tools/git_activity.py`
- `codeowners_tools/repo.py`
- `codeowners_tools/groups.py`

_No automated vulnerabilities were confirmed exploitable during this static analysis; however, the above findings describe realistic attack paths that should be prioritized for remediation._
