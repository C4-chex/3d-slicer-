"""Run inside an isolated Slicer --testing process; never write source DICOM."""
import hashlib
import json
import os
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import qt
import slicer
import vtk
from DICOMLib import DICOMUtils

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "verification" / "review-20261001-v2"
SOURCE = Path(r"D:\dicom文件\dcm")
OUT.mkdir(parents=True, exist_ok=True)
report = {"started_at": datetime.now().isoformat(), "status": "running", "checks": []}


def write_report():
    (OUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def check(name, condition, **details):
    report["checks"].append({"name": name, "passed": bool(condition), **details})
    write_report()
    if not condition:
        raise AssertionError(name)


def digest_files(files):
    return [hashlib.sha256(path.read_bytes()).digest() for path in files]


def run():
    source_files = sorted(SOURCE.glob("*.dcm"))
    source_before = digest_files(source_files)
    exit_code = 1
    try:
        slicer.util.selectModule("CT3D")
        w = slicer.modules.ct3d.widgetRepresentation().self()
        errors = []
        w.showError = lambda message: errors.append(str(message))
        report["slicer_version"] = slicer.app.applicationVersion
        check("module_setup", hasattr(w, "centerROIButton") and not w.reviewBox.enabled)
        window = slicer.util.mainWindow()
        window.resize(1440, 1000)
        window.show()
        slicer.app.processEvents()

        with DICOMUtils.TemporaryDICOMDatabase(str(OUT / "temporary-database")) as database:
            DICOMUtils.importDicom(str(SOURCE), database, copyFiles=False)
            w.importFolder = str(SOURCE.resolve())
            w.populateSeries()
            valid = [i for i, info in enumerate(w.series)
                     if info["modality"] == "MR" and not info["errors"] and len(info["files"]) == 20]
            blocked = [info for info in w.series if info["errors"]]
            check("real_mr_index", len(valid) >= 2, source_files=len(source_files), series_count=len(w.series))
            check("duplicate_positions_blocked", any(len(info["files"]) == 40 for info in blocked))
            first, second = valid[:2]
            w.seriesCombo.setCurrentIndex(first)
            w.onLoadSeries()
            check("real_mr_load", w.nodeIsPresent(w.currentVolume) and not errors, errors=errors)
            volume = w.currentVolume
            vr = w.currentVRDisplay
            check("volume_rendering_ready", w.nodeIsPresent(vr), detail=w._renderIssue)
            slicer.app.processEvents()
            manager = slicer.app.layoutManager()
            panes = [manager.sliceWidget(n) for n in ("Red", "Yellow", "Green")] + [manager.threeDWidget(0)]
            sizes = [(p.width, p.height) for p in panes]
            check("equal_four_pane_layout", max(x[0] for x in sizes)-min(x[0] for x in sizes) <= 3
                  and max(x[1] for x in sizes)-min(x[1] for x in sizes) <= 3, pane_sizes=sizes)
            pixels = slicer.util.arrayFromVolume(volume)
            pixel_digest = hashlib.sha256(pixels.tobytes()).digest()
            check("mr_geometry", tuple(volume.GetImageData().GetDimensions()) == (256, 256, 20),
                  dimensions=list(volume.GetImageData().GetDimensions()), spacing=list(volume.GetSpacing()))
            # Compare actual loaded voxel-center coordinates to every source DICOM plane origin.
            ijk_to_ras, ras_to_ijk = vtk.vtkMatrix4x4(), vtk.vtkMatrix4x4()
            volume.GetIJKToRASMatrix(ijk_to_ras)
            volume.GetRASToIJKMatrix(ras_to_ijk)
            deviations = []
            for path in w.loadedQuality["files"]:
                lps = w.numbers(w.tag(database, path, "0020,0032"))
                expected_ras = [-lps[0], -lps[1], lps[2], 1]
                ijk = ras_to_ijk.MultiplyPoint(expected_ras)
                actual = ijk_to_ras.MultiplyPoint([0, 0, round(ijk[2]), 1])
                deviations.append(float(np.linalg.norm(np.array(actual[:3])-expected_ras[:3])))
            check("dicom_plane_origins_match_loaded_geometry", max(deviations) < .01,
                  maximum_error_mm=max(deviations), planes=len(deviations))
            reference_file = w.loadedQuality["files"][0]
            lps = np.array(w.numbers(w.tag(database, reference_file, "0020,0032")))
            orientation = np.array(w.numbers(w.tag(database, reference_file, "0020,0037")))
            in_plane_spacing = w.numbers(w.tag(database, reference_file, "0028,0030"))
            plane_k = round(ras_to_ijk.MultiplyPoint([-lps[0], -lps[1], lps[2], 1])[2])
            corner_errors = []
            for i, j in ((0, 0), (255, 0), (0, 255), (255, 255)):
                expected = (lps + orientation[:3]*in_plane_spacing[1]*i + orientation[3:]*in_plane_spacing[0]*j)*[-1, -1, 1]
                actual = np.array(ijk_to_ras.MultiplyPoint([i, j, plane_k, 1])[:3])
                corner_errors.append(float(np.linalg.norm(actual-expected)))
            check("dicom_in_plane_orientation_and_scale", max(corner_errors) < .01, maximum_error_mm=max(corner_errors))
            import SimpleITK as sitk
            reference_pixels = sitk.GetArrayFromImage(sitk.ReadImage(reference_file)).reshape(pixels.shape[1:])
            signal_difference = float(np.max(np.abs(reference_pixels.astype(float)-pixels[plane_k].astype(float))))
            check("loaded_mr_pixel_values_match_single_file_read", signal_difference < .001, maximum_signal_difference=signal_difference)
            w.interpolateCheck.checked = False
            check("nearest_slice_mode", not volume.GetDisplayNode().GetInterpolate()
                  and vr.GetVolumePropertyNode().GetVolumeProperty().GetInterpolationType() == 1,
                  checkbox=bool(w.interpolateCheck.checked), slice_interpolate=volume.GetDisplayNode().GetInterpolate(),
                  volume_interpolation=vr.GetVolumePropertyNode().GetVolumeProperty().GetInterpolationType())
            w.planeCombo.setCurrentIndex(1)
            native_z = np.array([ijk_to_ras.GetElement(i, 2) for i in range(3)])
            native_z /= np.linalg.norm(native_z)
            alignments, acquired_indexes = [], []
            for n in ("Red", "Yellow", "Green"):
                matrix = manager.sliceWidget(n).mrmlSliceNode().GetSliceToRAS()
                alignments.append(abs(np.dot(native_z, [matrix.GetElement(i, 2) for i in range(3)])))
                if alignments[-1] > .9999:
                    acquired_indexes.append(ras_to_ijk.MultiplyPoint([matrix.GetElement(i, 3) for i in range(3)]+[1])[2])
            check("acquisition_plane_aligned_and_snapped", max(alignments) > .9999 and
                  all(abs(k-round(k)) < .0001 for k in acquired_indexes), alignment=max(alignments))
            w.planeCombo.setCurrentIndex(0)
            w.interpolateCheck.checked = True
            check("linear_display_mode", bool(volume.GetDisplayNode().GetInterpolate())
                  and vr.GetVolumePropertyNode().GetVolumeProperty().GetInterpolationType() == 1)
            fit_checks = []
            for n in ("Red", "Yellow", "Green"):
                pane = manager.sliceWidget(n)
                bounds = [0.0]*6
                pane.sliceLogic().GetVolumeSliceBounds(volume, bounds)
                fov = pane.mrmlSliceNode().GetFieldOfView()
                fit_checks.append(fov[0] >= bounds[1]-bounds[0]-.01 and fov[1] >= bounds[3]-bounds[2]-.01)
            check("all_slice_extents_fit_view", all(fit_checks))
            scales = [manager.sliceWidget(n).mrmlSliceNode().GetFieldOfView()[0]/manager.sliceWidget(n).sliceView().width
                      for n in ("Red", "Yellow", "Green")]
            check("common_slice_scale", max(scales)-min(scales) < .002, mm_per_logical_pixel=scales)
            check("mr_ct_presets_disabled", not w.softButton.enabled and not w.lungButton.enabled and not w.boneButton.enabled)
            w.windowSpin.value = 500.0
            w.levelSpin.value = 200.0
            w.applyManualWindowLevel()
            display = volume.GetDisplayNode()
            check("manual_window_level", not display.GetAutoWindowLevel() and abs(display.GetWindow()-500) < .01 and abs(display.GetLevel()-200) < .01)
            w.autoButton.click()
            check("auto_window_level", bool(display.GetAutoWindowLevel()))

            # This tests state isolation, not real CT loading or HU correctness.
            fake_candidate = dict(w.currentQuality, uid="synthetic-candidate", modality="CT", huEligible=True)
            w.series.append(fake_candidate)
            w.onSeriesChanged(len(w.series)-1)
            old_wl = (display.GetWindow(), display.GetLevel())
            w.applyWindowLevel(2000, 400)
            check("candidate_cannot_apply_ct_window_to_loaded_mr", not w.boneButton.enabled and old_wl == (display.GetWindow(), display.GetLevel()))
            w.series.pop()
            w.onSeriesChanged(first)

            w.crosshairCheck.checked = False
            check("crosshair_off", w.crosshairNode().GetCrosshairMode() == slicer.vtkMRMLCrosshairNode.NoCrosshair)
            w.crosshairCheck.checked = True
            check("crosshair_on", w.crosshairNode().GetCrosshairMode() == slicer.vtkMRMLCrosshairNode.ShowBasic)
            w.slicePlanesCheck.checked = True
            check("slice_planes_visible", all(slicer.app.layoutManager().sliceWidget(n).mrmlSliceNode().GetSliceVisible() for n in ("Red", "Yellow", "Green")))
            w.slicePlanesCheck.checked = False

            w.cropCheck.checked = True
            roi = w.roiNode()
            full_size = list(roi.GetSize())
            roi.SetSize([size * .5 for size in full_size])
            check("crop_changes_display", bool(vr.GetCroppingEnabled()) and np.allclose(roi.GetSize(), np.array(full_size)*.5))
            center = list(w.crosshairNode().GetCrosshairRAS())
            center[0] += 5.0
            w.crosshairNode().SetCrosshairRAS(center)
            w.centerROIButton.click()
            check("roi_follows_crosshair", np.allclose(roi.GetCenterWorld(), center))
            window_before = (display.GetWindow(), display.GetLevel())
            w.resetViewButton.click()
            check("reset_keeps_crop_and_contrast", bool(vr.GetCroppingEnabled()) and np.allclose(roi.GetSize(), np.array(full_size)*.5)
                  and np.allclose(window_before, (display.GetWindow(), display.GetLevel())))
            w.fitROIButton.click()
            check("roi_reset_full_extent", np.allclose(roi.GetSize(), full_size, atol=.01))
            w.cropCheck.checked = False
            w.renderPresetCombo.setCurrentIndex(1)
            check("mr_render_preset", vr.GetAttribute("CT3D.Preset") == "MR-Default")
            w.renderPresetCombo.setCurrentIndex(2)
            check("mip_uses_projection_mode", manager.threeDWidget(0).mrmlViewNode().GetRaycastTechnique() == slicer.vtkMRMLViewNode.MaximumIntensityProjection)
            w.renderPresetCombo.setCurrentIndex(0)
            check("standard_render_restores_composite_mode", manager.threeDWidget(0).mrmlViewNode().GetRaycastTechnique() == slicer.vtkMRMLViewNode.Composite)
            check("render_follows_display", bool(vr.GetFollowVolumeDisplayNode()))
            check("display_operations_preserve_voxels", hashlib.sha256(slicer.util.arrayFromVolume(volume).tobytes()).digest() == pixel_digest)

            from CT3DLib.CT3DExport import captureReviewImage, reviewDisplayParameters
            controllers = [slicer.app.layoutManager().sliceWidget(n).sliceController() for n in ("Red", "Yellow", "Green")]
            visibility_before = [not c.isHidden() for c in controllers]
            # Export here exercises the capture helper. The interactive consent dialog is not bypassed in the product.
            def collect_capture_state():
                check("snapshot_hides_corner_annotations", all(
                    not manager.sliceWidget(n).sliceView().cornerAnnotation().GetVisibility()
                    for n in ("Red", "Yellow", "Green")))
                check("snapshot_keeps_safe_orientation_labels", w._viewLabels is not None and
                      all(actor.GetVisibility() for _, actor in w._viewLabels.actors))
                return reviewDisplayParameters(w)
            params = captureReviewImage(str(OUT / "mr-four-views.png"), collect_capture_state)
            (OUT / "display-parameters.json").write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
            reader = vtk.vtkPNGReader()
            reader.SetFileName(str(OUT / "mr-four-views.png"))
            reader.Update()
            image = reader.GetOutput()
            check("four_view_png", image.GetDimensions()[0] > 300 and image.GetDimensions()[1] > 300
                  and image.GetScalarRange()[1]-image.GetScalarRange()[0] > 50, dimensions=list(image.GetDimensions()))
            from vtk.util.numpy_support import vtk_to_numpy
            dimensions = image.GetDimensions()
            screenshot = vtk_to_numpy(image.GetPointData().GetScalars()).reshape(dimensions[1], dimensions[0], -1)
            # VTK PNG rows start at the bottom: restrict to central 3D quadrant, away from text.
            roi_pixels = screenshot[int(dimensions[1]*.10):int(dimensions[1]*.40),
                                    int(dimensions[0]*.60):int(dimensions[0]*.90), :3].mean(axis=2)
            content_contrast = float(np.std(roi_pixels, axis=1).max())
            check("three_d_has_visible_content", content_contrast > 10, horizontal_contrast=content_contrast)
            check("snapshot_restores_controls", visibility_before == [not c.isHidden() for c in controllers])
            check("snapshot_records_geometry", not params["restorable_scene"] and len(params["slice_views"]) == 3
                  and params["volume_rendering"]["roi"] is not None)

            from ScreenCapture import ScreenCaptureLogic
            original_capture = ScreenCaptureLogic.captureImageFromView
            def fail_capture(*args, **kwargs):
                raise RuntimeError("QA simulated capture failure")
            ScreenCaptureLogic.captureImageFromView = fail_capture
            try:
                try:
                    captureReviewImage(str(OUT / "failed-capture.png"))
                except RuntimeError as exc:
                    check("snapshot_failure_reported", "QA simulated capture failure" in str(exc))
            finally:
                ScreenCaptureLogic.captureImageFromView = original_capture
            check("snapshot_failure_restores_controls", visibility_before == [not c.isHidden() for c in controllers])
            burned_flag = w.loadedQuality["burnedInAnnotation"]
            w.loadedQuality["burnedInAnnotation"] = "YES"
            try:
                w.onExportViewSnapshot()
                check("burned_in_yes_blocks_export", bool(errors) and "烧录" in errors[-1])
            finally:
                w.loadedQuality["burnedInAnnotation"] = burned_flag
                errors.clear()

            pair_a, pair_b = OUT / "qa-pair-a.bin", OUT / "qa-pair-b.bin"
            temp_a, temp_b = OUT / "qa-pair-a.tmp", OUT / "qa-pair-b.tmp"
            pair_a.write_bytes(b"old-a")
            pair_b.write_bytes(b"old-b")
            temp_a.write_bytes(b"new-a")
            temp_b.write_bytes(b"new-b")
            original_replace = os.replace
            def fail_second_replace(source, destination):
                if str(source) == str(temp_b):
                    raise OSError("QA simulated paired write failure")
                return original_replace(source, destination)
            os.replace = fail_second_replace
            try:
                try:
                    w.commitExportPair(((str(temp_a), str(pair_a)), (str(temp_b), str(pair_b))), "qa")
                except OSError:
                    pass
            finally:
                os.replace = original_replace
            check("paired_export_failure_restores_originals", pair_a.read_bytes() == b"old-a" and pair_b.read_bytes() == b"old-b")
            temp_a.write_bytes(b"new-a")
            w.commitExportPair(((str(temp_a), str(pair_a)), (str(temp_b), str(pair_b))), "qa")
            check("paired_export_success", pair_a.read_bytes() == b"new-a" and pair_b.read_bytes() == b"new-b")
            pair_a.unlink()
            pair_b.unlink()

            # Exercise the existing manual modelling workflow with a deliberately non-anatomical QA mask.
            def dismiss_information():
                modal = qt.QApplication.activeModalWidget()
                if isinstance(modal, qt.QMessageBox):
                    modal.accept()
            qt.QTimer.singleShot(300, dismiss_information)
            w.onStartSegmentation()
            check("segmentation_editor_binding", w.nodeIsPresent(w.currentSegmentation) and not errors, errors=list(errors))
            seg = w.currentSegmentation
            seg.GetSegmentation().GetSegment(w.currentSegmentID).SetName("QA non-anatomical mask")
            mask = np.zeros_like(pixels, dtype=np.uint8)
            z, y, x = np.array(mask.shape)//2
            mask[z-1:z+2, y-2:y+3, x-2:x+3] = 1
            slicer.util.updateSegmentBinaryLabelmapFromArray(mask, seg, w.currentSegmentID, volume)
            w.onUpdateSurface()
            poly = vtk.vtkPolyData()
            seg.GetClosedSurfaceRepresentation(w.currentSegmentID, poly)
            check("manual_mask_surface", poly.GetNumberOfPoints() > 0 and w.exportMeshButton.enabled, points=poly.GetNumberOfPoints())
            linear = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode", "QA spatial transform")
            seg.SetAndObserveTransformNodeID(linear.GetID())
            w.onSaveSegmentation()
            check("transformed_segmentation_save_guard", bool(errors) and ".mrb" in errors[-1])
            errors.clear()
            seg.SetAndObserveTransformNodeID(volume.GetTransformNodeID())
            slicer.mrmlScene.RemoveNode(linear)
            slicer.util.selectModule("CT3D")

            original_count = slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLScalarVolumeNode")
            w.onLoadSeries()
            check("reload_same_series_reuses_volume", w.currentVolume == volume and slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLScalarVolumeNode") == original_count)
            w.seriesCombo.setCurrentIndex(second)
            w.onLoadSeries()
            check("switch_series", w.currentVolume != volume and not vr.GetVisibility() and not errors, errors=errors)
            w.seriesCombo.setCurrentIndex(first)
            w.onLoadSeries()
            check("return_to_cached_series", w.currentVolume == volume and w.currentVRDisplay == vr)
            check("segmentation_retained_on_switch", w.currentSegmentation == seg and seg.GetDisplayNode().GetVisibility())

            # Inject a loader failure with a new temporary node and check rollback.
            before_ids = {slicer.mrmlScene.GetNthNode(i).GetID() for i in range(slicer.mrmlScene.GetNumberOfNodes())}
            previous_get = DICOMUtils.getLoadablesFromFileLists
            def fail_loader(*args, **kwargs):
                slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode", "CT3D QA temporary")
                raise RuntimeError("QA simulated decoder failure")
            DICOMUtils.getLoadablesFromFileLists = fail_loader
            w.currentQuality = dict(w.loadedQuality, uid="qa-failure")
            try:
                w.onLoadSeries()
            finally:
                DICOMUtils.getLoadablesFromFileLists = previous_get
                w.currentQuality = w.loadedQuality
            after_ids = {slicer.mrmlScene.GetNthNode(i).GetID() for i in range(slicer.mrmlScene.GetNumberOfNodes())}
            check("failed_load_rolls_back", w.currentVolume == volume and after_ids == before_ids)
            check("failure_reported", bool(errors) and "QA simulated decoder failure" in errors[-1])
            errors.clear()

            slicer.mrmlScene.Clear(0)
            check("scene_clear_resets_controls", w.currentVolume is None and not w.reviewBox.enabled and not w.exportViewButton.enabled)
        report["status"] = "passed"
        exit_code = 0
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
    finally:
        report["source_dicom_unchanged"] = source_before == digest_files(source_files)
        report["finished_at"] = datetime.now().isoformat()
        report["code_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (ROOT / "CT3D" / "CT3D.py",
                                              ROOT / "CT3D" / "CT3DLib" / "CT3DReview.py",
                                              ROOT / "CT3D" / "CT3DLib" / "CT3DExport.py",
                                              ROOT / "CT3D" / "CT3DLib" / "CT3DViewLabels.py")}
        write_report()
        slicer.app.exit(exit_code)


write_report()
run()
