"""One-command proof that the shipped local loop actually runs.

Starts only the services that are missing, refreshes one real T2 day card
through the private MLX adapter, then checks the same store over REST, the Diary
and MCP. T3 is read-only throughout: a verification run must not leave a
synthetic preference in durable memory to prove that writes work.

Two behaviours are deliberately different from the shell script this replaces:

- Logs survive a failure. The shell printed "see $TMP_DIR" from a handler that
  had already deleted it, so the one message you needed pointed at nothing. The
  directory is now kept when the run fails and removed when it passes.
- Readiness probes use httpx instead of shelling out to curl, so curl is no
  longer a dependency of proving the project works.

Every progress line is flushed. Child processes inherit this stdout and write to
it unbuffered, so a block-buffered parent would land its START/READY lines after
their output whenever the run is redirected to a log — which is exactly when
someone is reading it to find out what broke.

Everything else is the same, including the rule that only services this run
started are shut down afterwards.

中文说明：这是一条命令即可证明已交付的本地闭环确实能跑起来的验证流程。
它只启动缺失的服务，通过私有 MLX adapter 重新提取一张真实的 T2 日卡，然后
分别通过 REST、Diary 和 MCP 三条路径检查同一份存储。全程 T3 只读:验证
运行不能在持久记忆里留下一条合成出来的偏好，来"证明"写入是可用的。相比
它所取代的 shell 脚本，这里有两处刻意的行为差异:失败时日志会被保留下来
(原来的 shell 脚本会在已经删除临时目录之后才打印"见 $TMP_DIR"，导致
唯一需要的那条信息指向了一个不存在的地方，现在失败时保留目录、成功时才
删除)；服务就绪探测改用 httpx 而不是调用 curl，于是 curl 不再是"证明
项目能跑"这件事本身的依赖。每一行进度输出都会被立即 flush:子进程继承的
是这份未加缓冲的 stdout，如果父进程走块缓冲，一旦这次运行被重定向到日志
文件，START/READY 这些行就会跑到子进程输出的后面——而这恰恰是有人在读
日志排查问题的时候。其余部分行为不变，包括"只关掉本次运行自己启动的服务"
这条规则。
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
MLX_MODELS = "http://127.0.0.1:8080/v1/models"
API_HEALTH = "http://127.0.0.1:8000/healthz"
DIARY_API = "http://127.0.0.1:3000/api/diary"

EMBEDDER = "bge-m3"


class VerifyError(RuntimeError):
    """A precondition failed or a service never became ready.

    中文：某个前置条件不满足，或某个服务始终没能变为就绪状态。
    """


@dataclass
class Plan:
    """What this run would have to start, decided by probing only.

    中文：仅凭探测得出的结论——这次运行需要启动哪些东西。
    """

    docker: bool = False
    db: bool = False
    redis: bool = False
    ollama: bool = False
    mlx: bool = False
    api: bool = False
    web: bool = False
    next_build: bool = False
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Format the plan as an aligned, human-readable checklist.

        中文：把此计划渲染成对齐的、人类可读的清单文本。

        Returns:
            The formatted plan text. 格式化后的计划文本。
        """
        rows = [
            ("Docker Desktop", self.docker),
            ("Postgres (compose db)", self.db),
            ("Redis (compose redis)", self.redis),
            ("Ollama", self.ollama),
            ("Qwen2.5-3B + private MLX adapter", self.mlx),
            ("FastAPI", self.api),
            ("Next.js build", self.next_build),
            ("Next.js diary", self.web),
        ]
        lines = [
            f"  {'START' if needed else 'in place':>9}  {label}" for label, needed in rows
        ]
        return "\n".join(lines + [f"  {'note':>9}  {note}" for note in self.notes])


def _probe(url: str, timeout: float = 2.0) -> bool:
    """Whether a GET to `url` succeeds with a non-error status code.

    中文：向 url 发送 GET 请求，检查是否返回非错误状态码。
    """
    try:
        return httpx.get(url, timeout=timeout).status_code < 400
    except Exception:
        return False


def _compose_running(service: str) -> bool:
    """Whether the named compose service currently has a running container.

    中文：检查该 compose 服务当前是否有正在运行的容器。
    """
    result = subprocess.run(
        ["docker", "compose", "ps", "--status", "running", "-q", service],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _docker_up() -> bool:
    """Whether the Docker daemon answers. Docker 守护进程是否有响应。"""
    return (
        subprocess.run(
            ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode
        == 0
    )


def _preflight(root: Path) -> None:
    """Raise VerifyError if a required local artifact or command is missing.

    中文：如果缺少必需的本地产物或命令(venv、mlx_lm.server、私有 adapter、
    docker CLI)，就抛出 VerifyError。

    Args:
        root: Repository root to check artifacts under. 用于检查产物是否
            存在的仓库根目录。

    Raises:
        VerifyError: Naming the first missing prerequisite found. 指出发现
            的第一个缺失的前置依赖。
    """
    required = [
        (root / ".venv/bin/python", "missing .venv; install requirements first"),
        (root / ".venv/bin/mlx_lm.server", "mlx_lm.server is not installed in .venv"),
        (
            root / "train/outputs/mlx-adapters/adapters.safetensors",
            "missing private adapter: train/outputs/mlx-adapters/adapters.safetensors",
        ),
    ]
    for path, message in required:
        if not path.exists():
            raise VerifyError(message)
    if subprocess.run(["which", "docker"], stdout=subprocess.DEVNULL).returncode != 0:
        raise VerifyError("Docker CLI is not installed")


def build_plan(root: Path) -> Plan:
    """Probe every service without starting anything.

    中文：仅探测每一项服务的当前状态，不实际启动任何东西。

    Args:
        root: Repository root, used to check whether Next.js is built.
            仓库根目录，用于检查 Next.js 是否已完成构建。

    Returns:
        A Plan describing what would need to be started. 描述需要启动哪些
        服务的 Plan。
    """
    plan = Plan()
    plan.docker = not _docker_up()
    if plan.docker:
        # Container state cannot be read while the daemon is down, so those two
        # lines would be a guess. Say so instead of guessing.
        # 中文：守护进程没起来的时候，容器状态是读不到的，所以下面这两行
        # 只能是猜测。与其瞎猜，不如直接说明情况。
        plan.db = plan.redis = True
        plan.notes.append("db/redis assumed missing: the Docker daemon is not answering")
    else:
        plan.db = not _compose_running("db")
        plan.redis = not _compose_running("redis")
    plan.ollama = not _probe(OLLAMA_TAGS)
    plan.mlx = not _probe(MLX_MODELS)
    plan.api = not _probe(API_HEALTH)
    plan.web = not _probe(DIARY_API)
    plan.next_build = plan.web and not (root / ".next/BUILD_ID").is_file()
    return plan


def _wait_for(
    label: str, url: str, attempts: int, process: subprocess.Popen | None = None
) -> None:
    """Poll `url` once a second until it answers, the process dies, or attempts run out.

    中文：每秒轮询一次 url，直到它有响应、对应进程提前退出，或轮询次数用尽。

    Args:
        label: Human-readable name printed on success/failure. 成功或失败
            时打印使用的可读名称。
        url: Readiness endpoint to poll. 用于轮询就绪状态的端点。
        attempts: Maximum number of one-second polls. 最多轮询的秒数(次数)。
        process: If given, checked so a dead process fails fast instead of
            waiting out the full timeout. 若提供，则用于检测进程是否已
            提前退出，从而快速失败而不是等满整个超时时间。

    Raises:
        VerifyError: If the process exits early or attempts run out without
            success. 若进程提前退出，或轮询次数用尽仍未成功，则抛出。
    """
    for _ in range(attempts):
        if _probe(url):
            print(f"READY {label}", flush=True)
            return
        if process is not None and process.poll() is not None:
            raise VerifyError(f"{label} exited before becoming ready")
        time.sleep(1)
    raise VerifyError(f"{label} did not become ready")


def _spawn(
    command: list[str], log_path: Path, stack: contextlib.ExitStack
) -> subprocess.Popen:
    """Start a long-running child and register its shutdown.

    中文：启动一个长期运行的子进程，并把它的关闭动作注册进 ExitStack。

    Args:
        command: Argv to launch. 要启动的命令行参数列表。
        log_path: File the child's stdout/stderr are redirected to. 子进程
            stdout/stderr 重定向到的文件。
        stack: ExitStack that will terminate (then kill) the process on exit.
            退出时用于先 terminate、超时再 kill 该进程的 ExitStack。

    Returns:
        The spawned Popen handle. 已启动子进程的 Popen 句柄。
    """
    handle = stack.enter_context(log_path.open("w", encoding="utf-8"))
    process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT)

    def stop() -> None:
        """Terminate the child, escalating after a 10-second grace period.

        中文：先尝试 terminate，宽限期后仍未退出就 kill。
        """
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    stack.callback(stop)
    return process


def _quit_mac_app(name: str) -> None:
    """Ask a macOS app to quit via AppleScript. 通过 AppleScript 请求某个 macOS 应用退出。"""
    subprocess.run(
        ["osascript", "-e", f'tell application "{name}" to quit'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _check_embedder() -> None:
    """Raise VerifyError if Ollama does not have the embedding model pulled.

    中文：如果 Ollama 尚未拉取所需的嵌入模型，则抛出 VerifyError。

    Raises:
        VerifyError: If EMBEDDER is not among Ollama's tags. 若 EMBEDDER
            不在 Ollama 已拉取的模型列表中，则抛出。
    """
    payload = httpx.get(OLLAMA_TAGS, timeout=5).json()
    names = [model.get("name", "") for model in payload.get("models", [])]
    if not any(name.startswith(EMBEDDER) for name in names):
        raise VerifyError(
            f"Ollama model {EMBEDDER} is missing; run: ollama pull {EMBEDDER}"
        )


def _raise_interrupt(*_: object) -> None:
    """Signal handler that turns SIGTERM into a KeyboardInterrupt.

    中文：把 SIGTERM 信号转换成 KeyboardInterrupt 的信号处理函数，好让
    finally 块能正常执行清理。
    """
    raise KeyboardInterrupt


def run(root: Path, date: str | None = None, plan_only: bool = False) -> int:
    """Bring up whatever is missing, re-extract one T2 day, and verify the loop.

    中文：启动所有缺失的服务，重新提取一张 T2 日卡，并验证整条本地闭环。

    Args:
        root: Repository root. 仓库根目录。
        date: Which T2 day card to re-extract; None means the newest one.
            要重新提取的 T2 日卡日期；为 None 表示最新的一天。
        plan_only: If True, only probe and print what would be started, then
            return without starting anything. 若为 True，只探测并打印将要
            启动的内容，然后直接返回，不实际启动任何东西。

    Returns:
        0 on success (or on a `plan_only` preview), 1 on failure. 成功时
        (或仅预览计划时)返回 0；失败时返回 1。
    """
    try:
        _preflight(root)
        plan = build_plan(root)
    except VerifyError as error:
        print(f"FAIL  {error}", flush=True)
        return 1

    if plan_only:
        print("Would run scripts.verify_local_loop after bringing this up:", flush=True)
        print(plan.render(), flush=True)
        print("\nNothing was started. Drop --plan to run it.", flush=True)
        return 0

    # SIGTERM would otherwise bypass the finally block and leave a 3B model
    # resident and two web servers listening.
    # 中文：如果不这样处理，SIGTERM 会绕过 finally 块，留下一个常驻的 3B
    # 模型和两个仍在监听的 web 服务器没有被清理。
    previous_term = signal.signal(signal.SIGTERM, _raise_interrupt)
    temporary = Path(tempfile.mkdtemp(prefix="mindbridge-verify."))
    passed = False
    try:
        with contextlib.ExitStack() as stack:
            if plan.docker:
                print("START Docker Desktop", flush=True)
                subprocess.run(["open", "-gj", "-a", "Docker"], check=True)
                stack.callback(_quit_mac_app, "Docker")
                for _ in range(120):
                    if _docker_up():
                        break
                    time.sleep(1)
                if not _docker_up():
                    raise VerifyError("Docker Desktop did not become ready")
                plan.db = not _compose_running("db")
                plan.redis = not _compose_running("redis")

            print("START Postgres + Redis", flush=True)
            # Registered before `up` so a partial start is still torn down, and
            # only for the services that were not already serving something else.
            # 中文：先注册回调再执行 up，这样即使只启动到一半也会被清理干净；
            # 并且只针对本次运行之前没有在提供服务的那些服务这样做。
            for service, was_missing in (("redis", plan.redis), ("db", plan.db)):
                if was_missing:
                    stack.callback(
                        subprocess.run,
                        ["docker", "compose", "stop", service],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            subprocess.run(
                ["docker", "compose", "up", "-d", "--wait", "db", "redis"], check=True
            )

            if plan.ollama:
                print("START Ollama", flush=True)
                subprocess.run(["open", "-gj", "-a", "Ollama"], check=True)
                stack.callback(_quit_mac_app, "Ollama")
                _wait_for("Ollama", OLLAMA_TAGS, 60)
            _check_embedder()

            if plan.mlx:
                print("START Qwen2.5-3B + private MLX adapter", flush=True)
                process = _spawn(
                    [
                        str(root / ".venv/bin/mlx_lm.server"),
                        "--model", "mlx-community/Qwen2.5-3B-Instruct-4bit",
                        "--adapter-path", "train/outputs/mlx-adapters",
                        "--host", "127.0.0.1",
                        "--port", "8080",
                        "--max-tokens", "1200",
                        "--temp", "0.2",
                    ],
                    temporary / "mlx.log",
                    stack,
                )
                _wait_for("MLX server", MLX_MODELS, 180, process)

            if plan.api:
                print("START FastAPI", flush=True)
                process = _spawn(
                    [
                        str(root / ".venv/bin/uvicorn"),
                        "api.main:app",
                        "--host", "127.0.0.1",
                        "--port", "8000",
                    ],
                    temporary / "api.log",
                    stack,
                )
                _wait_for("FastAPI", API_HEALTH, 60, process)

            if plan.web:
                if plan.next_build:
                    print("BUILD Next.js", flush=True)
                    with (temporary / "next-build.log").open("w") as log:
                        build = subprocess.run(
                            ["npm", "run", "build"], stdout=log, stderr=subprocess.STDOUT
                        )
                    if build.returncode != 0:
                        raise VerifyError("npm run build failed")
                print("START Next.js diary", flush=True)
                process = _spawn(
                    [
                        "node_modules/.bin/next", "start",
                        "--hostname", "127.0.0.1",
                        "--port", "3000",
                    ],
                    temporary / "next.log",
                    stack,
                )
                _wait_for("Next.js diary", DIARY_API, 60, process)

            command = [str(root / ".venv/bin/python"), "-m", "scripts.verify_local_loop"]
            if date:
                command += ["--date", date]
            passed = subprocess.run(command).returncode == 0
            return 0 if passed else 1
    except VerifyError as error:
        print(f"FAIL  {error}", flush=True)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        if passed:
            for path in temporary.iterdir():
                path.unlink()
            temporary.rmdir()
        else:
            print(f"Service logs kept in {temporary}", flush=True)
