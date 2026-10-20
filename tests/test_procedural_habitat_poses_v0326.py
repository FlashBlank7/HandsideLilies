"""Regression: complete poses and a continuous mesh replace cut-up PNG strips."""
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_regenerated_habitat_poses_keep_contact_mask_cord_and_outfit(tmp_path):
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT/'scripts/verify_pose_repair.py'),
         '--root', str(PROJECT_ROOT), '--output', str(tmp_path/'pose-repair')],
        cwd=PROJECT_ROOT, env={**os.environ, 'PYTHONUTF8':'1'},
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=45, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report['passed']
    assert len(report['poses']) == 10
    assert len(report['outfits']) == 6
    for pose in report['poses']:
        assert pose['passed'] and pose['ready']
        assert pose['contactError'] < .5
        assert pose['cordInside'] and pose['cornerTransparent']
    assert all(outfit['passed'] for outfit in report['outfits'])


def test_breathing_does_not_reintroduce_independent_face_body_crops(tmp_path):
    source = (PROJECT_ROOT/'qml/V03PetBody.qml').read_text(encoding='utf-8')
    assert 'ContinuousPetImage {' in source
    assert 'id: headRegion' not in source
    assert 'id: shoulderRegion' not in source
    assert 'id: skirtRegion' not in source
    component = (PROJECT_ROOT/'qml/ContinuousPetImage.qml').read_text(encoding='utf-8')
    assert 'GridMesh' in component
    assert 'visible: !root.deformationReady' in component
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT/'scripts/verify_continuous_pet.py'),
         '--root', str(PROJECT_ROOT), '--output', str(tmp_path/'continuous')],
        cwd=PROJECT_ROOT, env={**os.environ,'PYTHONUTF8':'1'},
        capture_output=True, text=True, encoding='utf-8',errors='replace',
        timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report=json.loads(completed.stdout.strip().splitlines()[-1])
    assert report['passed'] and report['directionsChecked']==9
