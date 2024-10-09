# encoding=utf-8
import asyncio
import traceback
import time
from typing import Tuple
from res.configs import Config
from res.progress import get_progress, show_progress
from playwright.async_api import async_playwright, Playwright, Page, Browser
from playwright.async_api import TimeoutError
from playwright._impl._errors import TargetClosedError
from res.support import show_donate
from res.utils import optimize_page, get_lesson_name, video_optimize, get_filtered_class
import rich
from loguru import logger
import argparse
import sys

#Logger配置

def setup_logger(log_level):
    # 移除默认的日志处理器
    logger.remove()

    # 添加一个新的日志处理器，设置日志级别和格式
    logger.add(
        "Runtime_log_{time}.log",
        level=log_level.upper(),  # 设置日志级别为用户指定的级别
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        # 日志格式
        colorize=True,  # 启用颜色
        rotation="5 MB"
    )

#版本管理
def GetConfig(config: Config) -> Config:
    logger.info(f"版本: {config.version}")


# 获取全局事件循环
event_loop_verify = asyncio.Event()
event_loop_answer = asyncio.Event()


async def auto_login(config: Config, page: Page):
    await page.goto(config.login_url)
    await page.locator('#lUsername').fill(config.username)
    await page.locator('#lPassword').fill(config.password)
    await page.wait_for_timeout(500)
    await page.evaluate(config.login_js)
    await page.wait_for_selector(".wall-main", state="hidden")


async def init_page(p: Playwright, config: Config) -> Tuple[Page, Browser]:
    driver = "msedge" if config.driver == "edge" else config.driver
    if not config.exe_path:
        drivername = config.driver
        logger.info(f"正在启动{config.driver}浏览器...")
        browser = await p.chromium.launch(channel=driver, headless=False)
    else:
        print(f"正在启动{config.driver}浏览器...")
        browser = await p.chromium.launch(executable_path=config.exe_path, channel=driver, headless=False)

    context = await browser.new_context()
    page = await context.new_page()
    page.set_default_timeout(24 * 3600 * 1000)
    viewsize = await page.evaluate(
        '''() => {
            return {width: window.screen.availWidth, height: window.screen.availHeight};
        }'''
    )
    viewsize["height"] -= 50
    await page.set_viewport_size(viewsize)
    return page, browser


async def tail_work(page: Page, start_time, all_class, title) -> bool:
    reachTimeLimit = False
    page.set_default_timeout(24 * 3600 * 1000)
    time_period = (time.time() - start_time) / 60
    if 0 < config.limitMaxTime <= time_period:
        logger.info(f"\n当前课程已达时限:{config.limitMaxTime}min\n即将进入下门课程!")
        reachTimeLimit = True
    else:
        class_name = await all_class[-1].get_attribute('class')
        if "current_play" in class_name:
            logger.info("\n已学完本课程全部内容!")
            logger.info("==" * 10)
        else:
            logger.info(f"\"{title}\" Done !")
            logger.info(f"本次课程已学习:{time_period:.1f} min")
    return reachTimeLimit


async def play_video(page: Page) -> None:
    while True:
        try:
            await asyncio.sleep(0.5)
            playing = await page.query_selector(".pauseButton")
            if not playing:
                logger.debug("检测到被暂停,正在尝试播放.\n")
                canvas = await page.wait_for_selector(".videoArea", state="attached")
                await canvas.click()
        except TimeoutError:
            continue


async def skip_questions(page: Page, event_loop) -> None:
    while True:
        try:
            await asyncio.sleep(0.5)
            logger.info(f"出现选择题，正在自动处理...")
            await page.wait_for_selector(".topic-item", state="attached", timeout=1000)
            if not await page.query_selector(".answer"):
                choices = await page.locator(".topic-item").all()
                for each in choices:
                    await each.click()
                    await page.wait_for_timeout(200)
            await page.evaluate(config.close_ques)
            event_loop.set()
        except TimeoutError:
            logger.error(f"操作超时！")


async def wait_for_verify(page: Page, event_loop) -> None:
    while True:
        try:
            await asyncio.sleep(1)
            await page.wait_for_selector(".yidun_modal__title", state="attached", timeout=1000)
            logger.warning("\n检测到安全验证,请手动点击完成...")
            await page.wait_for_selector(".yidun_modal__title", state="hidden", timeout=24 * 3600 * 1000)
            event_loop.set()
            logger.info("\n安全验证已完成,继续播放...")
        except TimeoutError:
            logger.error(f"操作超时！")


async def learning_loop(page: Page, config: Config):
    title_selector = await page.wait_for_selector(".source-name")
    course_title = await title_selector.text_content()
    logger.info(f"当前课程:<<{course_title}>>")
    await page.wait_for_selector(".clearfix.video", state="attached")
    all_class = await get_filtered_class(page)
    start_time = time.time()
    cur_index = 0
    while cur_index < len(all_class):
        await all_class[cur_index].click()
        await page.wait_for_selector(".current_play", state="attached")
        await page.wait_for_timeout(1000)
        title = await get_lesson_name(page)
        logger.info("正在学习:%s",title)
        page.set_default_timeout(10000)
        try:
            await video_optimize(page, config)
        except TimeoutError:
            if await page.query_selector(".yidun_modal__title"):
                await event_loop_verify.wait()
        curtime, total_time = await get_progress(page)
        timer = 0
        while curtime != "100%":
            try:
                time_period = (time.time() - start_time) / 60
                timer += 1
                if 0 < config.limitMaxTime <= time_period:
                    break
                elif timer % 5 == 0:
                    curtime, total_time = await get_progress(page)
                    show_progress(desc="完成进度:", cur_str=curtime)
            except TimeoutError as e:
                if await page.query_selector(".yidun_modal__title"):
                    await event_loop_verify.wait()
                elif await page.query_selector(".topic-title"):
                    await event_loop_answer.wait()
                else:
                    logger.warn(f"\n{repr(e)}")
        if "current_play" in await all_class[cur_index].get_attribute('class'):
            cur_index += 1
        reachTimeLimit = await tail_work(page, start_time, all_class, title)
        if reachTimeLimit:
            return


async def reviewing_loop(page: Page, config: Config):
    title_selector = await page.wait_for_selector(".source-name")
    course_title = await title_selector.text_content()
    print(f"当前课程:<<{course_title}>>")
    await page.wait_for_selector(".clearfix.video", state="attached")
    all_class = await get_filtered_class(page, enableRepeat=True)
    course_start_time = time.time()
    cur_index = 0
    while cur_index < len(all_class):
        await all_class[cur_index].click()
        await page.wait_for_selector(".current_play", state="attached")
        await page.wait_for_timeout(1000)
        title = await get_lesson_name(page)
        logger.info("\n正在学习:%s", title)
        page.set_default_timeout(10000)
        try:
            await video_optimize(page, config)
        except TimeoutError:
            if await page.query_selector(".yidun_modal__title"):
                await event_loop_verify.wait()
        curtime, total_time = await get_progress(page)
        start_time = time.time()
        timer = 0
        while True:
            est_time = (time.time() - start_time) * config.limitSpeed
            if est_time > total_time:
                break
            try:
                time_period = (time.time() - course_start_time) / 60
                timer += 1
                if 0 < config.limitMaxTime <= time_period:
                    break
                elif timer % 5 == 0:
                    curtime, total_time = await get_progress(page)
                    show_progress(desc="完成进度:", cur_str=curtime)
            except TimeoutError as e:
                if await page.query_selector(".yidun_modal__title"):
                    await event_loop_verify.wait()
                elif await page.query_selector(".topic-title"):
                    await event_loop_answer.wait()
                else:
                    logger.warn(f"\n{repr(e)}")
        if "current_play" in await all_class[cur_index].get_attribute('class'):
            cur_index += 1
        reachTimeLimit = await tail_work(page, course_start_time, all_class, title)
        if reachTimeLimit:
            return


async def entrance(config: Config):
    tasks = []
    try:
        async with async_playwright() as p:
            page, browser = await init_page(p, config)
            # 进行登录
            if not config.username or not config.password:
                print("请手动输入账号密码...")
            print("等待登录完成...")
            await auto_login(config, page)
            # 启动协程任务
            skip_ques_task = asyncio.create_task(skip_questions(page, event_loop_answer))
            play_video_task = asyncio.create_task(play_video(page))
            verify_task = asyncio.create_task(wait_for_verify(page, event_loop_verify))
            tasks.extend([skip_ques_task, play_video_task, verify_task])
            # 遍历所有课程,加载网页
            for course_url in config.course_urls:
                print("开始加载播放页...")
                await page.goto(course_url)
                await page.wait_for_selector(".studytime-div")
                # 关闭弹窗,优化页面体验
                await optimize_page(page, config)
                # 启动课程主循环
                if config.enableRepeat:
                    await reviewing_loop(page, config)
                else:
                    await learning_loop(page, config)
        logger.info("\n\n")
        logger.info("所有课程学习完毕!")
        logger.info("改版作者留：如果你觉得有用的话，就请支持一下原作者吧！")
        show_donate("res/QRcode.jpg")
    except Exception as e:
        print(f"\n:{repr(e)}")
        if isinstance(e, TargetClosedError):
            logger.warning("检测到网页关闭,正在退出程序...")
    finally:
        # 结束所有协程任务
        await asyncio.gather(*tasks, return_exceptions=True) if tasks else None
        time.sleep(3)


if __name__ == "__main__":
    # 创建解析器
    parser = argparse.ArgumentParser(description="A script that logs messages with a specified log level.")
    # 添加日志级别的参数，默认是INFO
    parser.add_argument('--log-level', type=str, default='INFO',
                        help='The minimum log level to output (DEBUG, INFO, WARNING, ERROR, CRITICAL)')
    # 解析命令行参数
    args = parser.parse_args()
    # 配置日志
    setup_logger(args.log_level)
    logger.info("墨梓柒改版，原作者CXRunfree")
    config = Config()
    GetConfig(config)
    logger.info("===== Runtime Log =====")
    try:
        logger.info("正在载入数据...")
        config = Config()
        asyncio.run(entrance(config))
    except Exception as e:
        if isinstance(e, KeyError):
            logger.error("可能是account文件的配置出错!")
        elif isinstance(e, UserWarning):
            logger.error("是不是忘记填账号密码了?")
        elif isinstance(e, FileNotFoundError):
            logger.error(f"文件缺失: {e.filename}")
            logger.error("程序缺失依赖文件,请重新安装程序!")
        elif isinstance(e, TargetClosedError):
            logger.error("糟糕,网页关闭了!")
        elif isinstance(e, UnicodeDecodeError):
            logger.error("configs配置文件编码有误,保存时请选择utf-8或gbk!")
            logger.error(f"{e}")
        else:
            logger.error(f"{e}")
            logger.error("错误日志已保存至:log.txt")
            logger.error("系统出错,请检查后重新启动!")
