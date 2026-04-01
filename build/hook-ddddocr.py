"""PyInstaller hook for ddddocr — include ONNX model files."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("ddddocr")
