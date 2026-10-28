// Часы в шапке: дата и время, тик раз в секунду только на видимой вкладке.

// Форматтеры создаём один раз: Intl-объект дорогой, а тик — каждую секунду.
const CLOCK_DATE_FORMAT = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});
const CLOCK_TIME_FORMAT = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

export function initClock() {
  const clock = document.querySelector("#appClock");
  if (!clock) {
    return;
  }

  let timer = 0;

  const tick = () => {
    const now = new Date();
    clock.textContent = `${CLOCK_DATE_FORMAT.format(now)} · ${CLOCK_TIME_FORMAT.format(now)}`;
  };

  const stop = () => {
    if (timer) {
      window.clearInterval(timer);
      timer = 0;
    }
  };

  // На скрытой вкладке часы никто не видит — тик останавливаем.
  const start = () => {
    if (timer) {
      return;
    }
    tick();
    timer = window.setInterval(tick, 1000);
  };

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      stop();
    } else {
      start();
    }
  });

  start();
}
