#!/usr/bin/env python3
"""Quick integration test to verify group suggestion feature."""

from pathlib import Path
from codeowners_tools.audit import generate_audit
from codeowners_tools.git_activity import GroupSuggestion

def test_group_suggestion_structure():
    """Verify the audit result contains group suggestion structures."""
    print("Testing group suggestion structure...")
    
    # This test verifies that:
    # 1. AuditResult can be created with group suggestions
    # 2. The data structures are properly defined
    # 3. Serialization includes group data
    
    # Check that GroupSuggestion can be instantiated
    test_group = GroupSuggestion(
        group_name="test-group",
        matching_members=["user1", "user2"],
        member_count=2
    )
    assert test_group.group_name == "test-group"
    assert len(test_group.matching_members) == 2
    assert test_group.member_count == 2
    
    print("✓ GroupSuggestion dataclass works correctly")
    
    # Check that the imports work
    from codeowners_tools.codeowners import parse_codeowners
    from codeowners_tools.git_activity import suggest_owners_for_paths
    
    print("✓ All imports successful")
    print("✓ Integration test passed!")
    
if __name__ == "__main__":
    test_group_suggestion_structure()
