#!/usr/bin/env python3
"""
Cleanup script to remove cancelled projects from the database.

This script will:
1. Find all projects with status 'cancelled' in the database
2. Delete them permanently (including all stages, messages, and artifacts)

Usage:
    python cleanup_cancelled_projects.py [--dry-run]

Options:
    --dry-run    Show what would be deleted without actually deleting
"""

import sys
import argparse
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from database.dao import ProjectDAO


def cleanup_cancelled_projects(dry_run=False):
    """Remove all cancelled projects from the database."""
    dao = ProjectDAO()

    # Get all projects
    all_projects = dao.get_all_projects()

    # Filter cancelled projects
    cancelled_projects = [p for p in all_projects if p['status'] == 'cancelled']

    print(f"\n{'=' * 60}")
    print(f"Found {len(cancelled_projects)} cancelled project(s) in database")
    print(f"{'=' * 60}\n")

    if not cancelled_projects:
        print("✓ No cancelled projects to clean up!")
        return

    # Display cancelled projects
    for i, project in enumerate(cancelled_projects, 1):
        print(f"{i}. {project['name']}")
        print(f"   ID: {project['id']}")
        print(f"   Query: {project['research_query']}")
        print(f"   Created: {project['created_at']}")
        print()

    if dry_run:
        print("DRY RUN: No projects were deleted (use without --dry-run to actually delete)")
        return

    # Confirm deletion
    print(f"\n⚠️  This will permanently delete {len(cancelled_projects)} project(s) from the database!")
    response = input("Are you sure you want to continue? (yes/no): ").strip().lower()

    if response != 'yes':
        print("\n✗ Cancelled - no projects were deleted")
        return

    # Delete projects
    print("\nDeleting projects...")
    deleted_count = 0

    for project in cancelled_projects:
        try:
            dao.delete_project(project['id'])
            print(f"✓ Deleted: {project['name']} ({project['id']})")
            deleted_count += 1
        except Exception as e:
            print(f"✗ Failed to delete {project['name']}: {e}")

    print(f"\n{'=' * 60}")
    print(f"✓ Successfully deleted {deleted_count}/{len(cancelled_projects)} project(s)")
    print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up cancelled projects from the database",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without actually deleting'
    )

    args = parser.parse_args()

    print("\n🧹 Cancelled Projects Cleanup Script")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'DELETE'}\n")

    try:
        cleanup_cancelled_projects(dry_run=args.dry_run)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
