"""Run in a separate Slicer testing scene."""
import json
import math
import traceback
from pathlib import Path
import slicer
import vtk
import numpy as np

OUT = Path(__file__).resolve().parents[1] / "verification" / "measurements-20261004"
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": [], "status": "running"}

def check(name, result):
    report["checks"].append({"name": name, "passed": bool(result)})
    if not result:
        raise AssertionError(name)

try:
    slicer.util.selectModule("CT3D")
    w = slicer.modules.ct3d.widgetRepresentation().self()
    check("disabled_without_volume", not w.measurementBox.enabled)
    volume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
    slicer.util.updateVolumeFromArray(volume, np.zeros((10,10,10), dtype=np.int16))
    volume.CreateDefaultDisplayNodes()
    w.currentVolume = volume
    w.loadedQuality = {"modality": "MR"}
    w.updateMeasurementState()
    line = w.onAddMeasurement()
    selection = slicer.app.applicationLogic().GetSelectionNode()
    check("placement_uses_line_class", selection.GetActivePlaceNodeClassName() == "vtkMRMLMarkupsLineNode" and selection.GetActivePlaceNodeID() == line.GetID())
    check("bound_to_source", line.GetNodeReference("CT3D.SourceVolume") == volume)
    check("unfinished_not_exportable", not w.exportMeasurementsButton.enabled)
    line.AddControlPoint(vtk.vtkVector3d(0,0,0))
    line.AddControlPoint(vtk.vtkVector3d(3,4,0))
    record = w.measurementRecord(line, 1)
    check("five_mm_world_distance", abs(record["length_mm"] - 5) < 1e-9)
    check("lps_conversion", record["endpoints_world_lps_mm"][1] == [-3,-4,0])
    check("completed_export_enabled", w.exportMeasurementsButton.enabled)
    line.SetNthControlPointPosition(1,6,8,0)
    check("endpoint_edit_updates_label", "10.00 mm" in w.measurementText.text)
    transform = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLinearTransformNode")
    matrix = vtk.vtkMatrix4x4()
    matrix.SetElement(0,0,2); matrix.SetElement(1,1,2); matrix.SetElement(2,2,2)
    transform.SetMatrixTransformToParent(matrix)
    line.SetAndObserveTransformNodeID(transform.GetID())
    check("transformed_world_distance", abs(w.measurementRecord(line,1)["length_mm"]-20)<1e-9)
    volume.SetAndObserveTransformNodeID(transform.GetID())
    w.saveMeasurements(str(OUT / "lengths.json"))
    payload = json.loads((OUT / "lengths.json").read_text(encoding="utf-8"))
    check("export_world_geometry", payload["measurements"][0]["length_mm"] == 20 and payload["transform_present"])
    check("export_has_no_identity_or_source_reference", not any(k in json.dumps(payload) for k in ("PatientID", "PatientName", "uid", "SourceVolume", volume.GetID())))
    other = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")
    w.currentVolume = other
    w.updateMeasurementState()
    check("old_measurements_hidden_on_switch", not line.GetDisplayNode().GetVisibility() and not w.measurementNodes())
    w.onClearMeasurements()
    check("clear_other_preserves_original", w.nodeIsPresent(line))
    w.currentVolume = volume
    w.updateMeasurementState()
    check("return_restores_measurement", line.GetDisplayNode().GetVisibility())
    w.stopMeasurementPlacement()
    check("placement_ends", slicer.app.applicationLogic().GetInteractionNode().GetCurrentInteractionMode() == slicer.vtkMRMLInteractionNode.ViewTransform)
    w.onClearMeasurements()
    check("clear_removes_nodes_and_observers", not w.nodeIsPresent(line) and not w._measurementObservers)
    report["status"] = "passed"
except Exception:
    report["status"] = "failed"
    report["traceback"] = traceback.format_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    slicer.app.exit(0 if report["status"] == "passed" else 1)
