# encoding=utf-8
from playwright.async_api import Page, TimeoutError


async def move_mouse(page: Page):
    try:
        await page.wait_for_selector(".videoArea", state="attached", timeout=5000)
        elem = page.locator(".videoArea")
        await elem.hover(timeout=4000)
        pos = await elem.bounding_box()
        if not pos:
            return
        # Calculate the target position to move the mouse
        target_x = pos['x'] + 30
        target_y = pos['y'] + 30
        await page.mouse.move(target_x, target_y)
    except TimeoutError:
        return


async def get_progress(page: Page):
    def parse_time(h, m, s):
        return int(h) * 3600 + int(m) * 60 + int(s)

    curtime = "0%"
    await move_mouse(page)
    cur_play = await page.query_selector(".current_play")
    progress = await cur_play.query_selector(".progress-num")
    total_time_selector = await cur_play.query_selector(".time.fl")
    total_time_str = await total_time_selector.text_content()
    total_time = parse_time(*total_time_str.split(":"))
    if not progress:
        finish = await cur_play.query_selector(".time_icofinish")
        if finish:
            curtime = "100%"
    else:
        curtime = await progress.text_content()

    return curtime, total_time


from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from rich.console import Console

console = Console()


def show_progress(desc, cur_str=None, enableRepeat=False):
    # 将百分比字符串转换为整数
    percent = int(cur_str.strip('%'))

    # 如果是学习模式，并且进度达到或超过80%，则视为100%
    if percent >= 80 and not enableRepeat:
        percent = 100

    # 创建 Rich 的 Progress 对象
    with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
    ) as progress:
        # 添加任务到进度条
        task_id = progress.add_task(description=desc, total=100)

        # 更新任务进度
        progress.update(task_id, completed=percent)
