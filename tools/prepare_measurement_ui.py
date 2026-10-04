"""Load readonly MR sample for manual mouse-placement smoke test."""
from pathlib import Path
import slicer
from DICOMLib import DICOMUtils
import qt

def prepare():
    slicer.util.selectModule("CT3D")
    w = slicer.modules.ct3d.widgetRepresentation().self()
    folder = r"D:\dicom文件\dcm"
    out = Path(__file__).resolve().parents[1] / "verification" / "measurements-20261004" / "ui-database"
    out.mkdir(parents=True, exist_ok=True)
    DICOMUtils.openDatabase(str(out))
    DICOMUtils.importDicom(folder, slicer.dicomDatabase, copyFiles=False)
    w.importFolder = folder
    w.populateSeries()
    w.seriesCombo.setCurrentIndex(next(i for i,q in enumerate(w.series) if not q["errors"] and len(q["files"])==20))
    w.onLoadSeries()
    w.measurementBox.collapsed = False
    w.reviewBox.collapsed = True
    w.volumeBox.collapsed = True
    slicer.util.setPythonConsoleVisible(False)

qt.QTimer.singleShot(1500, prepare)
