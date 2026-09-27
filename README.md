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

## 双瓶封存与 B 瓶复检

样本按 A/B 双瓶管理，规则集中在 `src/rules.py`：

- `seal`：封存时分别登记 `seal_id_a` 和 `seal_id_b`（必填且不能相同），双瓶初始均为封存状态。
- `analyze`：实验室日常检测只处理 A 瓶，启封 A 瓶并记录结果（`adverse`/`normal`）。
- `request_b_retest`：A 瓶出现异常结果后，仅管理员可发起 B 瓶复检；A 瓶正常时 B 瓶继续封存。
- `open_b`：B 瓶须登记两名不同的见证人（且不能是经办人本人）后才能启封；手续不全则 B 瓶保持封存，并在错误信息中明确缺少哪一项。
- `analyze_b`：B 瓶启封后由实验室完成复检。

所有启封记录保留瓶号、封条号、启封时间、经办人和见证人，存放在样本的 `unseal_records` 中，同时写入审计时间线。演示页（`/`）可查看双瓶状态和启封记录，并按当前身份执行各操作。

## 主要接口

- `GET /health`：健康检查。
- `GET /api/<kind>`：按对象类型查询，可用`?status=`过滤。
- `POST /api/<kind>`：创建对象；请求体为JSON。
- `GET /api/entities/<id>`：读取对象当前版本。
- `POST /api/entities/<id>/actions`：提交`{"action":"动作名","data":{...},"expected_version":数字}`。
- `GET /api/audit`：读取审计记录，可用`?entity_id=`按对象过滤。

请求身份通过`X-User-Id`和`X-Role`请求头传入。创建和动作的可执行角色由规则引擎控制。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 局限

身份、实验室结果和听证材料均为原型模型，不替代正式反兴奋剂信息系统或证据鉴定流程。
