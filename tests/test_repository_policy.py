import importlib.util
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "repository_policy", Path(__file__).resolve().parents[1] / "scripts/check-repository.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class RepositoryPolicyTests(unittest.TestCase):
    def test_ordinary_work_can_share_the_current_or_next_patch(self):
        for version in ("0.4.1", "0.4.2", "0.4.2-beta.1", "0.4.2-rc.2"):
            with self.subTest(version=version):
                policy.validate_version(version, version, "v0.4.1")

    def test_patch_policy_rejects_minor_major_skipped_patch_and_downgrade(self):
        for version in ("0.5.0", "0.5.1", "1.0.0", "0.4.3", "0.4.0", "0.4.1-beta.1"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                policy.validate_version(version, version, "v0.4.1")

    def test_next_target_advances_only_after_a_new_published_baseline(self):
        policy.validate_version("0.4.3", "0.4.3", "v0.4.2")
        with self.assertRaises(ValueError):
            policy.validate_version("0.4.2", "0.4.2", "v0.4.3")

    def test_explicit_upgrade_levels_allow_only_their_next_target(self):
        policy.validate_version("0.5.0", "0.5.0", "v0.4.1", "minor")
        policy.validate_version("1.0.0", "1.0.0", "v0.4.1", "major")
        for version, level in (("0.6.0", "minor"), ("1.0.0", "minor"), ("2.0.0", "major")):
            with self.subTest(version=version, level=level), self.assertRaises(ValueError):
                policy.validate_version(version, version, "v0.4.1", level)

    def test_mismatched_metadata_and_nonstable_baseline_are_rejected(self):
        for package, metadata, baseline in (("0.4.2", "0.5.1", "v0.4.1"),
                                            ("0.4.2", "0.4.2", "v0.4.1-beta.1")):
            with self.subTest(package=package, baseline=baseline), self.assertRaises(ValueError):
                policy.validate_version(package, metadata, baseline)

    def test_malformed_versions_are_rejected(self):
        for value in ("0.4", "00.4.2", "0.4.2.1", "0.4.2-beta.0", "0.4.2-anything", "0.4.2\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.parse_version(value)

    def test_pr_requires_both_changelogs_and_unreleased_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in policy.CHANGELOGS:
                (root / name).write_text("# Changelog\n\n## Unreleased\n\n- Change\n")
            policy.validate_changelogs(root, policy.CHANGELOGS)
            for changed in ([], ["README.md"], ["CHANGELOG.md"]):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    policy.validate_changelogs(root, changed)
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## 0.4.2\n")
            with self.assertRaises(ValueError):
                policy.validate_changelogs(root, policy.CHANGELOGS)

    def test_real_package_metadata_agrees(self):
        package, metadata = policy.source_versions(policy.ROOT)
        self.assertEqual(package, metadata)
        policy.parse_version(package)
