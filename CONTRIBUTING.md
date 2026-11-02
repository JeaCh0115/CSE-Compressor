# 参与开发

谢谢愿意花时间。这个项目比较特殊的地方是：**它自己就是开发工具**——压缩率、启动耗时、界面每一帧都能量出来，所以"我觉得应该没问题"在这里不太算理由，跑个数字出来更有说服力。

## 环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

Windows 10/11 + Python 3.10+（开发机用 3.13）。引擎 `csengine/` 与命令行 `cscli/` 只用标准库，单独跑它们不需要装任何东西。

## 目录职责

| 目录 | 该放什么 | 不该放什么 |
|---|---|---|
| `csengine/` | 纯算法与格式实现，不 import `csgui`，不弹窗 | 任何界面/进程相关的东西 |
| `cscli/` | 命令行外壳，只做参数解析与输出 | 重复实现压缩逻辑 |
| `csgui/server.py` | 本地 HTTP + JSON API，只做"把引擎能力暴露成接口" | 业务算法 |
| `csgui/desktop.py` | 原生窗口、任务栏、单实例、DPI、窗口尺寸记忆 | 界面内容（那是 `web/` 的事） |
| `csgui/web/` | 单页前端，无构建步骤，直接改直接生效 | npm 依赖、打包工具 |
| `tools/` | 开发/验证工具：探针、打包、基准 | 运行期依赖 |
| `tests/` | 单元测试，**必须能在无图形环境跑通** | 需要真窗口的测试（放 `tools/*_probe.py`） |

## 改动流程

1. `python -m unittest discover -s tests -v` 先跑一遍，确认起点是绿的。
2. 改代码。界面相关改完顺手 `python tools/ui_probe.py`。
3. 如果修的是一个"只有真机才看得见"的问题（闪屏、焦点、窗口尺寸、原生提示），**加一个探针**而不是只靠截图口头保证：`tools/` 里现成的 `window_probe.py` / `frame_probe.py` / `tooltip_probe.py` 可以抄结构。
4. 更新 `README.md` 里对应的能力描述（这个仓库的 README 是功能清单，不是装饰）。

## 测试

```bash
python -m unittest discover -s tests        # 97 项，约 10 秒
python tools/ui_probe.py                    # 55 项界面回归，需要 Edge
python tools/dev_pipeline.py                # 全流水线：clean → compile → 测试 → 互操作矩阵 → 冒烟
```

`tools/*_probe.py` 会开真窗口、动真鼠标，所以：

- 别在锁屏 / 远程桌面断开时跑，会拿不到前台窗口（部分探针会自己 SKIP）。
- 探针会把窗口尺寸写进用户设置，跑完记得让它还原（现成的探针都做了）。
- 收尾别用 `WM_CLOSE`，会触发 `_save_geometry` 污染窗口记忆。

## 代码风格

- 4 空格缩进，行宽 ~100，`snake_case`，模块内私有用 `_` 前缀。
- 注释写**为什么**，不写**是什么**；这个仓库里凡是"看起来多此一举"的代码，通常都是被某个真实 bug 逼出来的，注释会把那个 bug 记下来。
- 前端沿用现有写法：原生 DOM + 模板字符串，不引入框架，不引入构建步骤。
- 新增第三方依赖前先问一句"标准库能不能到"，目前运行期依赖只有 3 个。

## 提交信息

一句话说清改了什么，正文写清"为什么"和"怎么验证的"：

```
修复小窗回车直接退出

紧凑窗移入时 WebView2 把焦点给了对话框的关闭按钮，回车等于点它。
quietInitialFocus() 把焦点转给对话框本体，并在面板就绪后补两次。

验证：tools/compact_test_probe.py 7/7，CDP 发 Enter 后窗口仍在
```

## 行为准则

不写"这不难你自己看"这类回复。新人问的问题，通常说明 README 或代码注释没写清楚——那本身就是个可以修的 bug。
