# 反兴奋剂检测与结果管理

这是一个只使用Python标准库和SQLite的模块化项目，默认端口为`8301`。所有业务规则集中在`src/rules.py`，`app.py`只负责组装依赖和启动服务。

## 模块结构

- `app.py`：命令行参数、依赖组装、启动和信号处理。
- `src/domain.py`：角色、数据结构、领域异常和基础校验。
- `src/rules.py`：状态机、权限、领域计算、冲突和跨对象校验。
- `src/repository.py`：SQLite建表、查询、事务和乐观锁。
- `src/service.py`：用例编排、幂等处理、版本控制和审计写入。
- `src/http_api.py`：HTTP路由、请求解析和统一错误响应。
- `src/audit.py`：实体操作审计时间线。
- `static/index.html`：最小演示页面。
- `tests/`：完整流程、规则和失败场景测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8301
```

服务启动时会自动建表。`--host`可修改监听地址，`--db`可指定其他SQLite文件。

## 核心对象

- `athlete`：运动员；`sample`：检测样本；`case`：结果管理案件。

## A/B瓶封存与复检

- `seal` 必须分别登记 `seal_a`、`seal_b` 两个封条号，且两者不能相同。
- `analyze` 为实验室日常检测，只启封A瓶并记录启封时间、经办人。
- A瓶异常（`report_adverse`）后，管理员可 `request_b_retest` 发起B瓶复检。
- `register_witness` 登记见证人（不可重复）；登记满2名后才允许 `open_b` 启封B瓶，
  手续不全时B瓶保持封存，并在错误信息中明确缺少哪一项。
- A瓶结果正常（`clear`）时B瓶继续封存。
- `open_b` 后由实验室 `record_b_result` 记录B瓶复检结果。
- 所有启封记录保存在样本的 `unseal_records`（瓶号、封条号、时间、经办人、见证人）；
  样本查询结果附带 `bottle_view` 展示双瓶状态与B瓶缺少的手续。
- 演示页 `/` 可查看双瓶状态和启封记录，并提供三个一键演示场景
  （B瓶复检全流程 / A瓶正常 / 手续不全无法启封）。

## 主要接口

- `GET /health`：健康检查。
- `GET /api/<kind>`：按对象类型查询，可用`?status=`过滤。
- `POST /api/<kind>`：创建对象；请求体为JSON。
- `GET /api/entities/<id>`：读取对象当前版本。
- `POST /api/entities/<id>/actions`：提交`{"action":"动作名","data":{...},"expected_version":数字}`。
- `GET /api/audit`：读取审计记录。

请求身份通过`X-User-Id`和`X-Role`请求头传入。创建和动作的可执行角色由规则引擎控制。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 局限

身份、实验室结果和听证材料均为原型模型，不替代正式反兴奋剂信息系统或证据鉴定流程。
