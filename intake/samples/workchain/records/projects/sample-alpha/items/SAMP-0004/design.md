# SAMPLE 设计 · SAMP-0004

## 现状
夹具的这一条原本只用一个 `design/` 目录，目录里的文件名由人定。

## 方案
改成单文件 `design.md`，四节按 `intake/document-skeletons.yaml` 的声明写。
这样夹具上就同时出现了两种解析规则：目录（`plan/`、`report/`）和带后缀的
单文件（`design.md`）。

## 影响面
只影响夹具。真实记录的 `design/` 目录不受影响 —— 解析规则里目录仍然排在
带后缀的单文件之前。

## 风险
夹具和真实布局漂移。这条的风险由 `tests/test_workchain.py` 承担：它跑真实
记录的同一条代码路径。
