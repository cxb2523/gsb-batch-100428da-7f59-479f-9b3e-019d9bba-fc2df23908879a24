"""
索引源节点处理器
从指定文件和列提取索引值列表
"""

from typing import List, Optional

from .base import AbstractNodeProcessor
from ..models import (
    BaseNode,
    IndexSourceInput,
    IndexSourceOutput,
    IndexValue,
    GlobalContext,
    PathContext,
    BranchContext,
    NodeType,
)
from ..utils.index_normalizer import build_index_manifest


class IndexSourceProcessor(AbstractNodeProcessor[IndexSourceInput, IndexSourceOutput]):
    """索引源节点处理器"""

    def __init__(self):
        super().__init__(NodeType.INDEX_SOURCE)

    def process(
        self,
        node: BaseNode,
        input_data: IndexSourceInput,
        global_context: GlobalContext,
        path_context: PathContext,
        branch_context: BranchContext = None,
    ) -> IndexSourceOutput:
        """
        处理索引源节点

        Args:
            node: 节点配置
            input_data: 空输入（索引源节点无输入）
            global_context: 全局上下文
            path_context: 路径上下文
            branch_context: 分支上下文

        Returns:
            索引值列表输出（按列提取时同时返回可核对的索引清单）
        """
        exec_id = ""
        try:
            exec_id = self.analyzer.onStart(node.id, self.node_type.value)
            data = node.data
            source_file_id = data.get("sourceFileID")
            sheet_name = data.get("sheetName")
            column_name = data.get("columnName")
            by_column = data.get("byColumn", True)

            if not source_file_id:
                raise ValueError("sourceFileID is required for index source node")

            if by_column:
                output = self._process_by_column(
                    global_context, source_file_id, sheet_name, column_name
                )
            else:
                output = self._process_by_sheet_name(global_context, source_file_id)

            self.analyzer.onFinish(exec_id)
            return output
        except Exception as e:
            self.analyzer.onError(exec_id, str(e))
            raise e

    # ------------------------------------------------------------------
    # 按列提取
    # ------------------------------------------------------------------
    def _process_by_column(
        self,
        global_context: GlobalContext,
        source_file_id: str,
        sheet_name: Optional[str],
        column_name: Optional[str],
    ) -> IndexSourceOutput:
        """按列提取索引值，并生成可核对索引清单"""
        file_label = self._describe_file(global_context, source_file_id)
        sheet_label = f"'{sheet_name}'" if sheet_name else "(missing)"
        column_label = f"'{column_name}'" if column_name else "(missing)"

        # 配置缺失同样要指出文件、工作表、列名
        if not sheet_name or not column_name:
            raise ValueError(
                "sheetName and columnName are required when byColumn is True "
                f"(file: {file_label}, sheet: {sheet_label}, "
                f"column: {column_label})"
            )

        if source_file_id not in global_context.files:
            raise ValueError(
                f"File {file_label} not found in context "
                f"(sheet: {sheet_label}, column: {column_label})"
            )

        # 校验工作表存在（错误信息必须包含文件、工作表、列名）
        file_info = global_context.files[source_file_id]
        available_sheets = [
            meta.get("sheet_name") for meta in getattr(file_info, "sheet_metas", [])
        ]
        if available_sheets and sheet_name not in available_sheets:
            raise ValueError(
                f"Sheet {sheet_label} not found in file {file_label} "
                f"for column {column_label}. Available sheets: {available_sheets}"
            )

        # 同时拿到原始与清洗后的列：清洗后用于确定索引值（与下游节点数据一致），
        # 原始值用于生成可核对的清单（保留首尾空白、数字/文本原始类型）
        try:
            raw_df, cleaned_df = self.load_raw_and_cleaned_dataframes_from_file(
                global_context, source_file_id, sheet_name
            )
        except Exception as e:
            # 任何读取失败都补充文件 / 工作表 / 列名定位信息后重新抛出
            raise ValueError(
                f"Failed to read sheet {sheet_label} of file {file_label} "
                f"for column {column_label}: {e}"
            ) from e

        # 校验列存在（错误信息必须包含文件、工作表、列名）
        if column_name not in cleaned_df.columns:
            available = [str(col) for col in cleaned_df.columns]
            raise ValueError(
                f"Column {column_label} not found in sheet {sheet_label} "
                f"of file {file_label}. Available columns: {available}"
            )

        if column_name not in raw_df.columns:
            raise ValueError(
                f"Column {column_label} not found in sheet {sheet_label} "
                f"of file {file_label}"
            )

        cleaned_values: List = cleaned_df[column_name].tolist()
        raw_values: List = raw_df[column_name].tolist()

        # 正常情况下两侧行数一致；读取异常时给出明确信息而不是返回半截清单
        if len(raw_values) != len(cleaned_values):
            raise ValueError(
                f"Column {column_label} in sheet {sheet_label} of file "
                f"{file_label} could not be read consistently "
                f"(raw rows: {len(raw_values)}, cleaned rows: "
                f"{len(cleaned_values)})"
            )

        index_values, manifest, manifest_summary = build_index_manifest(
            raw_values=raw_values, cleaned_values=cleaned_values
        )

        return IndexSourceOutput(
            index_values=index_values,
            source_column=column_name,
            index_manifest=manifest,
            manifest_summary=manifest_summary,
        )

    # ------------------------------------------------------------------
    # 按工作表名提取（保持原有行为，不生成清单）
    # ------------------------------------------------------------------
    def _process_by_sheet_name(
        self, global_context: GlobalContext, source_file_id: str
    ) -> IndexSourceOutput:
        """按sheet名提取索引值"""
        if not source_file_id or source_file_id not in global_context.files:
            raise ValueError(f"File {source_file_id} not found")

        file_info = global_context.files[source_file_id]

        # 提取所有非空sheet名作为索引值
        sheet_names = []
        for sheet_meta in file_info.sheet_metas:
            if sheet_meta.get("sheet_name"):
                sheet_names.append(sheet_meta["sheet_name"])

        index_values = [IndexValue(name) for name in sheet_names]

        return IndexSourceOutput(index_values=index_values, source_column=None)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    @staticmethod
    def _describe_file(global_context: GlobalContext, source_file_id: str) -> str:
        """构造错误信息中的文件描述（文件名 + 路径，找不到时退回文件ID）"""
        file_info = global_context.files.get(source_file_id) if global_context else None
        if file_info is None:
            return f"'{source_file_id}'"

        name = getattr(file_info, "name", None) or source_file_id
        path = getattr(file_info, "path", None)
        if path:
            return f"'{name}' ({path})"
        return f"'{name}'"

    def validate_node_config(self, node: BaseNode) -> bool:
        """验证节点配置"""
        if not super().validate_node_config(node):
            return False

        data = node.data
        source_file_id = data.get("sourceFileID")
        by_column = data.get("byColumn", True)

        if not source_file_id:
            return False

        if by_column:
            # 按列模式需要sheet名和列名
            return bool(data.get("sheetName") and data.get("columnName"))
        else:
            # 按sheet名模式只需要文件ID
            return True
