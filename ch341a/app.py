"""Flet GUI for the CH341A SPI flash tool.

Dark, tabbed layout styled after the user's reference screenshot: a slim
top bar, a header with an icon + title/subtitle, a horizontal tab strip,
and bordered "card" panels holding outlined fields and pill-shaped action
buttons with a status label next to them.
"""

from __future__ import annotations

import subprocess

import flet as ft

from .device import CH341ADevice, CH341AError, CH341ANotFoundError
from .spi_flash import SpiFlash

BG = "#1e1e1e"
BORDER = "#3a3a3a"
TEXT_PRIMARY = "#f0f0f0"
TEXT_SECONDARY = "#b3b3b3"
ACCENT = "#455a64"
ACCENT_GREEN = "#388e3c"
WARN_COLOR = "#c9a24b"
DANGER = "#d32f2f"
DANGER_TEXT = "#d32f2f"


def _outlined_field(label: str, **kwargs) -> ft.TextField:
    return ft.TextField(
        label=label,
        border=ft.InputBorder.OUTLINE,
        border_color=BORDER,
        border_radius=8,
        bgcolor=BG,
        color=TEXT_PRIMARY,
        label_style=ft.TextStyle(color=TEXT_SECONDARY),
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        **kwargs,
    )


def _pill_button(text: str, icon, on_click, bgcolor: str = ACCENT) -> ft.Button:
    return ft.Button(
        text,
        icon=icon,
        icon_color=ft.Colors.WHITE,
        color=ft.Colors.WHITE,
        bgcolor=bgcolor,
        on_click=on_click,
        style=ft.ButtonStyle(
            shape=ft.StadiumBorder(),
            padding=ft.Padding.symmetric(horizontal=20, vertical=16),
        ),
    )


def _card(title: str, controls: list) -> ft.Container:
    return ft.Container(
        border=ft.Border.all(1, BORDER),
        border_radius=12,
        padding=20,
        content=ft.Column(
            [
                ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Divider(height=16, color=BORDER),
                *controls,
            ],
            spacing=14,
        ),
    )


async def main(page: ft.Page) -> None:
    page.title = "CH341A SPI Flash Tool"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BG
    page.padding = 0
    page.window.width = 760
    page.window.height = 640

    log_view = ft.ListView(spacing=4, auto_scroll=True, expand=True)

    def ui(fn) -> None:
        """Schedule fn() on the page's event loop; safe to call from a worker thread."""

        async def _runner():
            fn()

        page.run_task(_runner)

    def log(message: str, color: str = TEXT_PRIMARY) -> None:
        def _apply():
            log_view.controls.append(ft.Text(message, size=12, color=color, selectable=True))
            log_view.update()

        ui(_apply)

    def set_text(control: ft.Text, value: str, color: str | None = None) -> None:
        def _apply():
            control.value = value
            if color is not None:
                control.color = color
            control.update()

        ui(_apply)

    def set_progress(bar: ft.ProgressBar, value: float) -> None:
        def _apply():
            bar.value = value
            bar.update()

        ui(_apply)

    def notify(title: str, message: str) -> None:
        """Best-effort desktop notification for long-running ops (read/write/
        erase can take minutes with no other feedback if the window isn't
        being watched). Only ever called from the worker thread these tasks
        already run on (via page.run_thread), so the blocking subprocess
        call here doesn't touch the UI event loop. Must never affect the
        operation itself - swallow anything if notify-send is missing or
        fails."""
        try:
            subprocess.run(
                ["notify-send", "--app-name=CH341A Tool", title, message],
                check=False,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def set_busy(busy: bool) -> None:
        def _apply():
            for c in action_buttons:
                c.disabled = busy
            page.update()

        ui(_apply)

    # -- "Device" tab --------------------------------------------------

    device_status_text = ft.Text("Не подключено", color=TEXT_SECONDARY, size=13)
    id_text = ft.Text("ID: —", color=TEXT_SECONDARY, size=13, selectable=True)

    def check_device_task() -> None:
        set_busy(True)
        set_text(device_status_text, "Проверка...", TEXT_SECONDARY)
        try:
            with CH341ADevice() as dev:
                info = SpiFlash(dev).read_id()
            set_text(device_status_text, "Подключено", ACCENT_GREEN)
            set_text(id_text, f"ID: {info}")
            log(f"Устройство найдено. JEDEC ID: {info}")
        except CH341ANotFoundError:
            set_text(device_status_text, "Не подключено", TEXT_SECONDARY)
            log("Программатор не найден. Подключите CH341A и повторите.", WARN_COLOR)
        except CH341AError as exc:
            set_text(device_status_text, "Ошибка", DANGER_TEXT)
            log(f"Ошибка связи с устройством: {exc}", DANGER_TEXT)
        finally:
            set_busy(False)

    def on_check_click(e: ft.Event) -> None:
        page.run_thread(check_device_task)

    check_btn = _pill_button("Проверить устройство", ft.Icons.USB, on_check_click)

    device_tab = _card(
        "Устройство",
        [
            ft.Row([check_btn, device_status_text], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            id_text,
        ],
    )

    # -- "Read" tab ------------------------------------------------------

    read_path_field = _outlined_field("Файл для сохранения дампа", value="dump.bin", expand=True)
    read_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=BORDER)
    read_status_text = ft.Text("", color=TEXT_SECONDARY, size=13)

    save_picker = ft.FilePicker()

    async def on_browse_read(e: ft.Event) -> None:
        path = await save_picker.save_file(
            dialog_title="Куда сохранить дамп",
            file_name="dump.bin",
            allowed_extensions=["bin"],
        )
        if path:
            read_path_field.value = path
            read_path_field.update()

    def read_task(path: str) -> None:
        set_busy(True)
        set_progress(read_progress, 0)

        def progress_cb(done, total):
            set_progress(read_progress, done / total if total else 0)

        try:
            with CH341ADevice() as dev:
                flash = SpiFlash(dev)
                info = flash.read_id()
                log(f"Обнаружен чип: {info}")
                log(f"Чтение {info.size_bytes} байт...")
                data = flash.read_all(progress_cb=progress_cb)
            with open(path, "wb") as f:
                f.write(data)
            log(f"Готово: {len(data)} байт сохранено в {path}")
            set_text(read_status_text, "Готово", ACCENT_GREEN)
            notify("CH341A: чтение завершено", f"Дамп сохранён в {path}")
        except CH341ANotFoundError:
            log("Программатор не найден. Подключите CH341A и повторите.", WARN_COLOR)
            set_text(read_status_text, "Устройство не найдено", TEXT_SECONDARY)
            notify("CH341A: ошибка", "Программатор не найден")
        except CH341AError as exc:
            log(f"Ошибка чтения: {exc}", DANGER_TEXT)
            set_text(read_status_text, "Ошибка", DANGER_TEXT)
            notify("CH341A: ошибка чтения", str(exc))
        except OSError as exc:
            log(f"Не удалось сохранить файл: {exc}", DANGER_TEXT)
            set_text(read_status_text, "Ошибка файла", DANGER_TEXT)
            notify("CH341A: ошибка файла", str(exc))
        finally:
            set_busy(False)

    def on_read_click(e: ft.Event) -> None:
        path = (read_path_field.value or "").strip()
        if not path:
            log('Укажите файл для сохранения (кнопка "Обзор...").', WARN_COLOR)
            return
        set_text(read_status_text, "Выполняется...", TEXT_SECONDARY)
        page.run_thread(read_task, path)

    read_btn = _pill_button("Начать чтение", ft.Icons.SAVE, on_read_click)

    read_tab = _card(
        "Чтение / дамп чипа",
        [
            ft.Row(
                [read_path_field, ft.IconButton(icon=ft.Icons.FOLDER_OPEN, on_click=on_browse_read, icon_color=TEXT_SECONDARY)],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            read_progress,
            ft.Row([read_btn, read_status_text], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
    )

    # -- "Write" tab -------------------------------------------------------

    write_path_field = _outlined_field("Файл прошивки для записи", expand=True)
    verify_checkbox = ft.Checkbox(label="Проверять после записи", value=True, active_color=ACCENT)
    write_progress_label = ft.Text("Запись", size=12, color=TEXT_SECONDARY)
    write_progress = ft.ProgressBar(value=0, color=ACCENT, bgcolor=BORDER)
    verify_progress_label = ft.Text("Проверка", size=12, color=TEXT_SECONDARY)
    verify_progress = ft.ProgressBar(value=0, color=ACCENT_GREEN, bgcolor=BORDER)
    verify_progress_block = ft.Column(
        [verify_progress_label, verify_progress], spacing=4, visible=verify_checkbox.value
    )
    write_status_text = ft.Text("", color=TEXT_SECONDARY, size=13)

    def on_verify_checkbox_change(e: ft.Event) -> None:
        verify_progress_block.visible = verify_checkbox.value
        verify_progress_block.update()

    verify_checkbox.on_change = on_verify_checkbox_change

    open_picker = ft.FilePicker()

    async def on_browse_write(e: ft.Event) -> None:
        files = await open_picker.pick_files(
            dialog_title="Файл прошивки для записи",
            allowed_extensions=["bin"],
            allow_multiple=False,
        )
        if files:
            write_path_field.value = files[0].path
            write_path_field.update()

    def write_task(path: str, verify: bool) -> None:
        set_busy(True)
        set_progress(write_progress, 0)
        set_progress(verify_progress, 0)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            log(f"Не удалось прочитать файл {path}: {exc}", DANGER_TEXT)
            set_text(write_status_text, "Ошибка файла", DANGER_TEXT)
            notify("CH341A: ошибка файла", str(exc))
            set_busy(False)
            return

        def progress_cb(done, total):
            set_progress(write_progress, done / total if total else 0)

        def verify_progress_cb(done, total):
            set_progress(verify_progress, done / total if total else 0)

        try:
            with CH341ADevice() as dev:
                flash = SpiFlash(dev)
                info = flash.read_id()
                log(f"Обнаружен чип: {info}")
                if info.size_bytes and len(data) > info.size_bytes:
                    raise CH341AError(
                        f"Файл ({len(data)} байт) больше чипа ({info.size_bytes} байт)"
                    )
                log("Снятие защиты записи (Off-Protect)...")
                log(f"Запись {len(data)} байт из {path}...")
                flash.write(
                    0,
                    data,
                    verify=verify,
                    progress_cb=progress_cb,
                    verify_progress_cb=verify_progress_cb,
                )
            log("Запись завершена" + (" и проверена успешно." if verify else "."))
            set_text(write_status_text, "Готово", ACCENT_GREEN)
            notify(
                "CH341A: запись завершена",
                "Прошивка записана" + (" и проверена успешно." if verify else "."),
            )
        except CH341ANotFoundError:
            log("Программатор не найден. Подключите CH341A и повторите.", WARN_COLOR)
            set_text(write_status_text, "Устройство не найдено", TEXT_SECONDARY)
            notify("CH341A: ошибка", "Программатор не найден")
        except CH341AError as exc:
            log(f"Ошибка записи: {exc}", DANGER_TEXT)
            set_text(write_status_text, "Ошибка", DANGER_TEXT)
            notify("CH341A: ошибка записи", str(exc))
        finally:
            set_busy(False)

    def on_write_click(e: ft.Event) -> None:
        path = (write_path_field.value or "").strip()
        if not path:
            log('Укажите файл прошивки (кнопка "Обзор...").', WARN_COLOR)
            return
        set_text(write_status_text, "Выполняется...", TEXT_SECONDARY)
        page.run_thread(write_task, path, verify_checkbox.value)

    write_btn = _pill_button("Начать запись", ft.Icons.UPLOAD_FILE, on_write_click)

    write_tab = _card(
        "Запись прошивки",
        [
            ft.Row(
                [write_path_field, ft.IconButton(icon=ft.Icons.FOLDER_OPEN, on_click=on_browse_write, icon_color=TEXT_SECONDARY)],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            verify_checkbox,
            write_progress_label,
            write_progress,
            verify_progress_block,
            ft.Row([write_btn, write_status_text], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(
                "Совет: сначала сделайте дамп на вкладке «Чтение» — это резервная копия прошивки.",
                size=12,
                color=TEXT_SECONDARY,
            ),
        ],
    )

    # -- "Erase" tab -------------------------------------------------------

    erase_status_text = ft.Text("", color=TEXT_SECONDARY, size=13)

    def erase_task() -> None:
        set_busy(True)
        try:
            with CH341ADevice() as dev:
                flash = SpiFlash(dev)
                log("Снятие защиты записи (Off-Protect)...")
                log("Стирание чипа целиком (может занять до пары минут)...")
                flash.erase_chip()
            log("Чип успешно стёрт.")
            set_text(erase_status_text, "Готово", ACCENT_GREEN)
            notify("CH341A: стирание завершено", "Чип успешно стёрт.")
        except CH341ANotFoundError:
            log("Программатор не найден. Подключите CH341A и повторите.", WARN_COLOR)
            set_text(erase_status_text, "Устройство не найдено", TEXT_SECONDARY)
            notify("CH341A: ошибка", "Программатор не найден")
        except CH341AError as exc:
            log(f"Ошибка стирания: {exc}", DANGER_TEXT)
            set_text(erase_status_text, "Ошибка", DANGER_TEXT)
            notify("CH341A: ошибка стирания", str(exc))
        finally:
            set_busy(False)

    def on_erase_click(e: ft.Event) -> None:
        def do_confirm(ev: ft.Event) -> None:
            page.pop_dialog()
            set_text(erase_status_text, "Выполняется...", TEXT_SECONDARY)
            page.run_thread(erase_task)

        def do_cancel(ev: ft.Event) -> None:
            page.pop_dialog()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Подтверждение"),
            content=ft.Text(
                "Стереть весь чип целиком? Действие необратимо.\n"
                "Рекомендуем сначала сделать дамп на вкладке «Чтение»."
            ),
            actions=[
                ft.TextButton("Отмена", on_click=do_cancel),
                ft.TextButton("Стереть", on_click=do_confirm, style=ft.ButtonStyle(color=DANGER_TEXT)),
            ],
        )
        page.show_dialog(dlg)

    erase_btn = _pill_button("Стереть весь чип", ft.Icons.DELETE_FOREVER, on_erase_click, bgcolor=DANGER)

    erase_tab = _card(
        "Стирание чипа",
        [
            ft.Row([erase_btn, erase_status_text], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(
                "Необратимая операция. Сделайте резервную копию перед стиранием.",
                color=WARN_COLOR,
                size=12,
            ),
        ],
    )

    # -- "Log" tab -----------------------------------------------------------

    log_tab = ft.Container(
        border=ft.Border.all(1, BORDER),
        border_radius=12,
        padding=16,
        expand=True,
        content=log_view,
    )

    action_buttons = [check_btn, read_btn, write_btn, erase_btn]

    tabs_content = [
        ft.Container(device_tab, padding=24),
        ft.Container(read_tab, padding=24),
        ft.Container(write_tab, padding=24),
        ft.Container(erase_tab, padding=24),
        ft.Container(log_tab, padding=24, expand=True),
    ]

    tabs = ft.Tabs(
        length=len(tabs_content),
        expand=True,
        content=ft.Column(
            [
                ft.Container(
                    padding=ft.Padding.only(left=24),
                    content=ft.TabBar(
                        tabs=[
                            ft.Tab(label="Устройство"),
                            ft.Tab(label="Чтение"),
                            ft.Tab(label="Запись"),
                            ft.Tab(label="Стирание"),
                            ft.Tab(label="Лог"),
                        ],
                        tab_alignment=ft.TabAlignment.START,
                        divider_color=BORDER,
                        indicator_color=ft.Colors.WHITE,
                        label_color=ft.Colors.WHITE,
                        unselected_label_color=TEXT_SECONDARY,
                    ),
                ),
                ft.TabBarView(controls=tabs_content, expand=True),
            ],
            expand=True,
        ),
    )

    top_bar = ft.Container(
        bgcolor=BG,
        padding=ft.Padding.symmetric(horizontal=20, vertical=12),
        border=ft.Border(bottom=ft.BorderSide(1, BORDER)),
        content=ft.Row([ft.Text("CH341A", weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY)], alignment=ft.MainAxisAlignment.END),
    )

    header = ft.Container(
        padding=ft.Padding.symmetric(horizontal=24, vertical=20),
        content=ft.Row(
            [
                ft.Container(
                    width=44,
                    height=44,
                    bgcolor="#2a2a2a",
                    border_radius=8,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Icon(ft.Icons.MEMORY, color=TEXT_SECONDARY),
                ),
                ft.Column(
                    [
                        ft.Text("CH341A SPI Flash Tool", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                        ft.Text("1A86:5512 — SPI NOR flash (25Q128 и совместимые)", size=12, color=TEXT_SECONDARY),
                    ],
                    spacing=2,
                ),
            ],
            spacing=14,
        ),
    )

    page.add(
        ft.Column(
            [top_bar, header, tabs],
            spacing=0,
            expand=True,
        )
    )


def run() -> None:
    ft.run(main)


if __name__ == "__main__":
    run()
