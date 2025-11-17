# Group Suggestion Feature - Implementation Summary

## Feature Overview
Added functionality to suggest CODEOWNERS groups based on top committers for paths that need ownership. The system:
1. Identifies top contributors (e.g., top 5 or top 3) for uncovered paths
2. Maps these contributors to their CODEOWNERS groups
3. Suggests groups that contain the most active contributors
4. Displays group suggestions alongside individual contributor suggestions

## What Was Changed

### 1. Data Structures (codeowners_tools/git_activity.py)
- **New `GroupSuggestion` dataclass**:
  ```python
  @dataclass
  class GroupSuggestion:
      group_name: str
      matching_members: List[str]
      member_count: int
  ```

- **Extended `CandidateSuggestion`**:
  - Added `groups: List[str]` field to track which groups each candidate belongs to

- **Extended `PathOwnershipSuggestion`**:
  - Added `group_suggestions: List[GroupSuggestion]` field

### 2. Group Mapping Logic (codeowners_tools/git_activity.py)
- **`_find_groups_for_identity(identity, groups)`**:
  - Maps a contributor identity to all groups they belong to
  - Searches through CODEOWNERS groups dict to find matching memberships

- **`_aggregate_group_suggestions(candidates, groups, top_n)`**:
  - Takes top N candidates and finds their groups
  - Counts how many top contributors belong to each group
  - Returns groups sorted by number of matching members

- **Extended `suggest_owners_for_paths()`**:
  - New parameters: `groups`, `suggest_groups`, `group_limit`
  - Attaches group memberships to each candidate
  - Generates group suggestions when enabled

### 3. Audit Orchestration (codeowners_tools/audit.py)
- **Extended `MaskSuggestion` dataclass**:
  - Added `group_suggestions: Optional[List[GroupSuggestion]]`

- **Updated `generate_audit()` function**:
  - New parameters: `suggest_groups: bool = False`, `group_limit: int = 5`
  - Passes groups dict from CODEOWNERS parse result to suggestion functions
  - Conditionally enables group suggestion based on `suggest_groups` flag

- **Updated serialization**:
  - `to_dict()` methods now include `groups` array in candidates
  - `group_suggestions` array serialized with group_name, matching_members, member_count

### 4. UI Form (scripts/run_ui.py)
- **Added form controls**:
  - Checkbox: "Suggest groups from CODEOWNERS" (default: checked)
  - Number input: "Top groups to suggest" (default: 5, min: 1)

- **Updated `_default_form_values()`**:
  ```python
  "suggest_groups": "on",
  "group_limit": "5",
  ```

- **Updated `_run_audit_from_form()`**:
  - Parses `suggest_groups` and `group_limit` from form
  - Passes these parameters to `generate_audit()`

### 5. UI Rendering (scripts/run_ui.py)
- **Enhanced `_render_suggestions()`**:
  - Shows group badges next to candidates: 🔖 N group(s)
  - Displays expandable group suggestions section: 📋 Suggested groups (N)
  - Group table shows: Group name, Matching members count, Member identities

- **Enhanced `_render_mask_suggestions()`**:
  - Same group badge and suggestions rendering as regular suggestions
  - Integrated seamlessly with existing mask pattern display

## How It Works

### Example Workflow:
1. User enables "Suggest groups from CODEOWNERS" checkbox
2. Audit runs and identifies `/src/frontend/` has no owner
3. Git analysis finds top 3 committers: alice, bob, carol (40%, 35%, 25%)
4. System checks CODEOWNERS groups:
   - alice is in @@frontend-team and @@ui-designers
   - bob is in @@frontend-team
   - carol is in @@frontend-team and @@qa-team
5. Aggregates group suggestions:
   - @@frontend-team: 3 matching members (alice, bob, carol)
   - @@ui-designers: 1 matching member (alice)
   - @@qa-team: 1 matching member (carol)
6. Displays suggestion:
   ```
   Directory: /src/frontend/ (total commits: 87)
   
   [Candidate table with group badges]
   alice (40%) 🔖 2 group(s)
   bob (35%) 🔖 1 group(s)
   carol (25%) 🔖 2 group(s)
   
   📋 Suggested groups (3)
   [Expandable details with group table]
   @@frontend-team - 3 members: alice, bob, carol
   @@ui-designers - 1 member: alice
   @@qa-team - 1 member: carol
   ```

## Configuration

### UI Controls:
- **Suggest groups from CODEOWNERS**: Enable/disable group suggestions
- **Top groups to suggest**: Number of groups to display (default: 5)

### Programmatic Usage:
```python
from codeowners_tools.audit import generate_audit

audit = generate_audit(
    repo_root=Path("/path/to/repo"),
    codeowners_path=Path("CODEOWNERS"),
    suggest_groups=True,  # Enable group suggestions
    group_limit=5,        # Show top 5 groups
    suggest_limit=3,      # Top 3 contributors
)

# Access group suggestions
for suggestion in audit.suggestions:
    if suggestion.group_suggestions:
        for group in suggestion.group_suggestions:
            print(f"Group: {group.group_name}")
            print(f"Members: {', '.join(group.matching_members)}")
```

## Testing

All changes compile successfully:
```bash
python -m py_compile scripts/run_ui.py
python -m py_compile codeowners_tools/audit.py
python -m py_compile codeowners_tools/git_activity.py
python test_integration.py  # ✓ Integration test passed!
```

## Files Modified

1. `codeowners_tools/git_activity.py` - Core group mapping logic
2. `codeowners_tools/audit.py` - Audit orchestration and data structures
3. `scripts/run_ui.py` - UI form controls and rendering
4. `run_ui.py` (root) - Entry point wrapper (no changes for this feature)

## Future Enhancements

Possible improvements:
- Add weighting: groups with higher total commit share ranked higher
- Filter groups by activity threshold (e.g., only suggest if >50% of top contributors)
- Export group suggestions to generated CODEOWNERS file
- Add group coverage statistics to dashboard
- Support nested group recommendations (groups that contain other groups)
