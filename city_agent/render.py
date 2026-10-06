from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .adapter import redact_text
from .schema import PredictionRecord


def _safe(value: object) -> str:
    if value is None:
        return "无"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        return "；".join(f"{key}：{_safe(item)}" for key, item in value.items()) or "未提取"
    if isinstance(value, (list, tuple)):
        return "；".join(_safe(item) for item in value) or "未提取"
    return redact_text(str(value))


def _remove_paragraph_borders(element: object) -> None:
    """Remove Word's built-in title rule from a style or paragraph."""
    ppr = element.get_or_add_pPr()
    for child in list(ppr):
        if child.tag == qn("w:pBdr"):
            ppr.remove(child)


def _table(document: Document, headers: list[str], rows: list[list[object]], widths: list[float] | None = None) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = False
    for index, header in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = header
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), "E8EEF5")
        cell._tc.get_or_add_tcPr().append(shading)
        for run in cell.paragraphs[0].runs:
            run.bold = True
            run.font.color.rgb = RGBColor(0, 0, 0)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = _safe(value)
            cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    if widths:
        for index, width in enumerate(widths):
            table.columns[index].width = Inches(width)
            for row in table.rows:
                row.cells[index].width = Inches(width)


def render_prediction_docx(record: PredictionRecord, output_path: str | Path) -> None:
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal.font.size = Pt(10.5)
    title_style = document.styles["Title"]
    title_style.font.color.rgb = RGBColor(0, 0, 0)
    title_style.font.size = Pt(16)
    _remove_paragraph_borders(title_style._element)
    heading_style = document.styles["Heading 1"]
    heading_style.font.color.rgb = RGBColor(0, 0, 0)
    title = document.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _remove_paragraph_borders(title._p)
    run = title.add_run("信息提取报告")
    run.bold = True
    document.add_paragraph(f"源文件：{_safe(record.file_name)}")
    if record.error:
        document.add_paragraph(f"错误信息：{_safe(record.error)}")
    if record.prediction is None:
        document.add_paragraph("模型未生成预测结果。本文件仅保留处理状态，不代表评测答案。")
    else:
        prediction = record.prediction
        document.add_paragraph("1、简要信息")
        document.add_paragraph("从报告中提取桥梁定检的概要结论与关键指标，输出内容包括：")
        _table(
            document,
            ["字段", "示例", "说明"],
            [
                ["桥梁名称", prediction.bridge_name, "桥梁全称"],
                ["报告编号", prediction.report_number, "检测报告编号"],
                ["报告日期", prediction.inspection_date, "报告出具日期"],
                ["总体评分", prediction.overall_score, "总体技术状况评分"],
                ["总体等级", prediction.overall_grade, "总体技术状况等级"],
                ["上部结构评分", prediction.component_scores.get("superstructure"), "上部结构技术状况评分"],
                ["上部结构等级", prediction.component_scores.get("superstructure_grade"), "上部结构技术状况等级"],
                ["下部结构评分", prediction.component_scores.get("substructure"), "下部结构技术状况评分"],
                ["下部结构等级", prediction.component_scores.get("substructure_grade"), "下部结构技术状况等级"],
                ["桥面系评分", prediction.component_scores.get("bridge_deck_system"), "桥面系技术状况评分"],
                ["桥面系等级", prediction.component_scores.get("bridge_deck_system_grade"), "桥面系技术状况等级"],
                ["上一次总体评分", None, "上一次定检总体技术状况评分"],
                ["上一次总体等级", None, "上一次定检总体技术状况等级"],
                ["病害发展趋势与具体说明", None, "与上一次定检相比病害的发展趋势和定量描述"],
                ["总体结论", prediction.summary, "总体结论的简要说明"],
                ["主要风险点", "；".join(prediction.key_risks), "比较严重或突出的病害描述"],
                ["建议", "；".join(prediction.recommendations), "显示建议的情况说明"],
            ], widths=[1.45, 3.55, 1.5],
        )
        document.add_paragraph("2、详细信息")
        document.add_paragraph("（1）详细结论")
        if prediction.summary:
            document.add_paragraph(_safe(prediction.summary))
        for item in prediction.key_risks:
            document.add_paragraph(_safe(item))
        document.add_paragraph("（2）建议明细")
        recommendation_rows = []
        for index, item in enumerate(prediction.recommendations, 1):
            category = "立即处置" if any(word in item for word in ("立即", "应急")) else "预防性养护" if any(word in item for word in ("日常", "加强", "标识", "规范")) else "尽快维修"
            recommendation_rows.append([index, category, item, ""])
        _table(document, ["序号", "建议类别", "建议内容", "病害部位"], recommendation_rows or [["", "", "", ""]], widths=[0.45, 1.0, 4.25, 0.8])
        document.add_paragraph("病害列表")
        _table(
            document,
            ["序号", "病害部位", "病害类型", "病害描述", "是否新增", "上一次定检状态", "发展程度"],
            [
                [
                    index,
                    item.location,
                    item.disease_type,
                    item.description,
                    item.is_new,
                    item.previous_status,
                    item.development,
                ]
                for index, item in enumerate(prediction.diseases, 1)
            ],
            widths=[0.4, 1.2, 1.0, 3.0, 0.65, 1.0, 0.8],
        )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(target)
