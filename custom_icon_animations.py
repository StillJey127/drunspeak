"""Переопределения анимаций иконок, сохраняемые между обновлениями клиента.

Автообновление исходной версии заменяет только client.pyw. Этот sidecar можно
настраивать отдельно; если его нет или он повреждён, используются встроенные
реализации методов из IconBtn.
"""


def _hex_rgb(value):
    value = str(value).lstrip("#")
    if len(value) < 6:
        value = "ffffff"
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


class IconAnimations:
    ANIM_MS = 16
    GROW_STEPS = 6
    PULSE_MS = 90

    def _animate_icon(self, target_scale):
        if self._icon_job:
            self.after_cancel(self._icon_job)
        start = self._scale
        steps = max(1, int(self.GROW_STEPS))

        def step(i):
            if not self.winfo_exists():
                return
            scale = start + (target_scale - start) * (i / steps)
            self._render_icon(scale)
            if i < steps:
                self._icon_job = self.after(self.ANIM_MS, lambda: step(i + 1))
            else:
                self._icon_job = None

        step(1)

    def _cur_color(self):
        if self._active:
            return self._colors["active_hover"] if self._hovering else self._colors["active"]
        return self._colors["hover"] if self._hovering else self._colors["bg"]

    def _on_enter(self, event):
        self._hovering = True
        self._animate_to(self._cur_color())
        self._animate_icon(1.14)

    def _on_leave(self, event):
        self._hovering = False
        self._animate_to(self._cur_color())
        self._animate_icon(1.0)

    def _on_click(self, event):
        self.flash()
        if self._command:
            self._command()

    def flash(self):
        """Короткая световая вспышка и кольцо при нажатии."""
        if self._flash_job:
            self.after_cancel(self._flash_job)
        base = self._cur_color()
        r, g, b = _hex_rgb(base)
        hot = "#%02x%02x%02x" % (
            min(255, int(r * 1.45) + 30),
            min(255, int(g * 1.45) + 30),
            min(255, int(b * 1.45) + 30))
        self._set_fill(hot)
        self.itemconfig(self._ring, outline=hot)
        self._flash_job = self.after(110, lambda: (
            self._set_fill(base) if self.winfo_exists() else None,
            self.itemconfig(self._ring, outline="") if self.winfo_exists() else None))

    def _animate_to(self, target):
        if self._color_job:
            self.after_cancel(self._color_job)
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None
        start_color, end_color = _hex_rgb(self._current_color), _hex_rgb(target)
        steps = 6

        def step(i):
            if not self.winfo_exists():
                return
            t = i / steps
            color = "#%02x%02x%02x" % tuple(
                int(a + (b - a) * t) for a, b in zip(start_color, end_color))
            self._set_fill(color)
            if i < steps:
                self._color_job = self.after(self.ANIM_MS, lambda: step(i + 1))
            else:
                self._color_job = None
                if self._active:
                    self._start_pulse()

        step(1)

    def _start_pulse(self):
        if self._pulse_job:
            return
        base = _hex_rgb(self._colors["active"])
        hot = _hex_rgb(self._colors["active_hover"])
        # Квантованный пульс ограничивает рост PhotoImage-кэша.
        palette = ["#%02x%02x%02x" % tuple(
            int(a + (b - a) * (k / 7)) for a, b in zip(base, hot))
            for k in range(8)]

        def tick():
            if not self.winfo_exists() or not self._active:
                self._pulse_job = None
                return
            self._pulse_step = (self._pulse_step + 1) % 14
            index = self._pulse_step if self._pulse_step < 8 else 14 - self._pulse_step
            self._set_fill(palette[max(0, min(7, index))])
            self._pulse_job = self.after(self.PULSE_MS, tick)

        tick()

    def set_active(self, active):
        if active == self._active:
            return
        self._active = bool(active)
        self._render_icon(self._scale)
        self._animate_to(self._cur_color())

    def is_active(self):
        return self._active


def apply_icon_animations(icon_button_class):
    """Устанавливает кастомные методы до создания первых кнопок."""
    for name in ("ANIM_MS", "GROW_STEPS", "PULSE_MS"):
        setattr(icon_button_class, name, getattr(IconAnimations, name))
    for name in (
            "_animate_icon", "_cur_color", "_on_enter", "_on_leave",
            "_on_click", "flash", "_animate_to", "_start_pulse",
            "set_active", "is_active"):
        setattr(icon_button_class, name, getattr(IconAnimations, name))
