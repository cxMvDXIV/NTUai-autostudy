/*
 * 南通大学 AIGC 课程平台刷课（浏览器版，不用装 Python）
 *
 * 用法：
 *   1. 用 Chrome 打开课程平台并登录，进到任意一门课的页面
 *   2. 按 F12 -> 切到 Console（控制台）
 *   3. 把这个文件的全部内容粘进去，回车
 *
 * 它直接用你浏览器里已经登录好的身份，不会把你网页端的登录顶掉。
 * 想调的参数都在下面 CONFIG 里，改完再粘一次即可。
 */
(async function run() {
  "use strict";

  const CONFIG = {
    elective: true,     // 要不要连选修课一起刷
    courseIds: [],      // 空数组 = 账号下所有课程；也可以写 [21] 或 [21, 20]
    steps: 6,           // 每节分几次上报
    gapMs: 2000,        // 每次上报之间等多久，毫秒
    limit: 0,           // 每门课本次最多刷几节，0 = 不限
    stopAt: 0,          // 课程进度到这个百分比就停，0 = 不停
  };

  const API = "https://xdplus.com.cn/html5/new_student_nantong_university_course_front/";
  const token = localStorage.getItem("acg-token");
  if (!token) {
    alert("没找到登录信息：先在这个浏览器里登录课程平台，再回来粘这段代码。");
    return;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fmtMin = (m) => {
    const t = Math.max(0, Math.round(m));
    return `${Math.floor(t / 60)}小时${String(t % 60).padStart(2, "0")}分`;
  };
  const fmtPct = (v) => (v === null || v === undefined ? String(v)
    : String(Number(v).toFixed(2)).replace(/\.?0+$/, ""));

  async function call(path, fields) {
    const fd = new FormData();
    Object.entries(fields || {}).forEach(([k, v]) => {
      if (v !== null && v !== undefined) fd.append(k, String(v));
    });
    const resp = await fetch(API + path, { method: "POST", headers: { token }, body: fd });
    let out;
    try {
      out = await resp.json();
    } catch (e) {
      throw new Error(`接口没返回 JSON（${path}），HTTP ${resp.status}`);
    }
    if (out.code === 401 || out.code === 2007) throw new Error("登录已过期，请重新登录后再来一次");
    if (out.code !== 200) throw new Error(`${out.msg || "接口报错"}（${path}）`);
    return out.data || {};
  }

  function flatten(detail) {
    const course = detail.course || {};
    const items = [];
    (detail.chapters || []).forEach((ch) => {
      (ch.periods || []).forEach((p) => {
        items.push({
          chapter_id: ch.id,
          period_id: p.id,
          period_name: p.period_name || "",
          need_min: Number(p.period_hour) || 0,
          done_min: Number(p.class_schedule) || 0,
          elective: Number(p.period_type || 0) === 1,
          locked: p.is_play === false,
        });
      });
    });
    return { course, items };
  }

  const scope = (items) => items.filter((x) => x.need_min > 0 && (CONFIG.elective || !x.elective));
  const stats = (items) => {
    const t = scope(items);
    return {
      sections: t.length,
      finished: t.filter((x) => x.done_min >= x.need_min).length,
      minutes: t.reduce((a, x) => a + Math.min(x.done_min, x.need_min), 0),
      required: t.reduce((a, x) => a + x.need_min, 0),
    };
  };

  // 光有 token 也能拿到 user_id，不用依赖 acg-user 里那个字段
  const me = await call("obtainingUserInformation", {});
  const uid = me.user_id;
  console.log(`已用浏览器里的登录身份：${me.user_name || ""} (user_id=${uid})`);

  const all = (await call(`courseList?user_id=${uid}&page=1&limit=50`, {})).list || [];
  if (!all.length) {
    console.log("这个账号下没有课程，检查一下是不是登录错了账号。");
    return;
  }
  const ids = CONFIG.courseIds && CONFIG.courseIds.length
    ? CONFIG.courseIds
    : all.map((c) => Number(c.id));
  console.log(`要处理 ${ids.length} 门课，${CONFIG.elective ? "含选修" : "只刷必修"}，`
    + `每节分 ${CONFIG.steps} 次上报，间隔 ${CONFIG.gapMs}ms`);

  const rows = [];
  const t0 = Date.now();
  for (const cid of ids) {
    let detail = await call("courseDetail", { user_id: uid, course_id: cid });
    let { course, items } = flatten(detail);
    const name = course.course_name || `course_id=${cid}`;
    console.log(`\n---- ${name} (course_id=${cid}) ----`);
    const before = stats(items);
    const beforePct = course.total_progress;
    const start = Date.now();
    let played = 0;

    for (let round = 1; round <= 8; round++) {
      const pending = items.filter((x) => x.need_min > 0 && (CONFIG.elective || !x.elective)
        && x.done_min < x.need_min);
      if (!pending.length) break;
      let moved = false;
      for (const x of pending) {
        if (CONFIG.limit && played >= CONFIG.limit) break;
        const fresh = flatten(await call("courseDetail", { user_id: uid, course_id: cid }));
        Object.assign(x, fresh.items.find((f) => f.period_id === x.period_id) || {});
        items = fresh.items;
        if (x.need_min <= 0 || x.done_min >= x.need_min) continue;
        if (x.locked && !x.elective) {
          console.log(`  跳过 ${x.period_id} ${x.period_name}：还锁着，等前面的课时解锁`);
          continue;
        }
        moved = true;
        const target = x.need_min;
        const step = Math.max(1, target / Math.max(1, CONFIG.steps));
        let report = Math.min(target, Math.max(x.done_min + 1, step));
        let stored = x.done_min;
        for (;;) {
          const out = await call("submitStudyDuration", {
            user_id: uid, course_id: cid, chapter_id: x.chapter_id, period_id: x.period_id,
            class_id: course.class_id, study_duration: Math.round(report * 60),
            period_type: x.elective ? 1 : 0,
          });
          stored = Number((out.data || {}).study_duration || 0);
          if (report >= target || stored >= target) break;
          if (CONFIG.gapMs) await sleep(CONFIG.gapMs);
          report = Math.min(target, report + step);
        }
        x.done_min = stored;
        played++;
        const pct = (await call("courseDetail", { user_id: uid, course_id: cid })).course.total_progress;
        console.log(`  [${round}] ${x.period_id} ${x.period_name} -> ${Math.round(stored)}/${Math.round(target)}分`
          + `  课程进度 ${fmtPct(pct)}%`);
        if (CONFIG.stopAt && pct && Number(pct) >= CONFIG.stopAt) { round = 99; break; }
        if (CONFIG.gapMs) await sleep(CONFIG.gapMs);
      }
      if (!moved) { console.log("  没有可上报的课时了（可能都被锁定），停止。"); break; }
    }

    detail = await call("courseDetail", { user_id: uid, course_id: cid });
    ({ course, items } = flatten(detail));
    const after = stats(items);
    rows.push({
      name, sections: after.sections, before, after,
      gainedSections: Math.max(0, after.finished - before.finished),
      gainedMin: Math.max(0, after.minutes - before.minutes),
      elapsed: (Date.now() - start) / 1000,
      pctBefore: beforePct, pctAfter: course.total_progress,
    });
    if (CONFIG.stopAt && Number(course.total_progress || 0) >= CONFIG.stopAt) {
      console.log(`\n整体已到目标进度 ${CONFIG.stopAt}%，后面的课不刷了。`);
      break;
    }
  }

  console.log("\n================ 本次刷课结果 ================");
  let touched = 0, movedCourses = 0, totSec = 0, totMin = 0, full = 0;
  rows.forEach((r) => {
    touched++;
    console.log(r.name);
    if (!r.gainedSections && !r.gainedMin) {
      console.log(r.sections && r.after.finished >= r.sections
        ? `  没有要刷的课时，${r.sections} 节已经全部刷满`
        : "  这一轮没有推进（课时可能还锁着，或已到上报上限）");
    } else {
      movedCourses++;
      console.log(`  刷了 ${r.gainedSections} 节，新增 ${fmtMin(r.gainedMin)}，用时 ${(r.elapsed / 60).toFixed(1)} 分钟`);
    }
    console.log(`  完成节数 ${r.before.finished}/${r.sections} -> ${r.after.finished}/${r.sections}，`
      + `已学 ${fmtMin(r.before.minutes)} -> ${fmtMin(r.after.minutes)}`
      + `（本次范围要求 ${fmtMin(r.after.required)}）`);
    console.log(`  平台显示进度 ${fmtPct(r.pctBefore)}% -> ${fmtPct(r.pctAfter)}%`);
    totSec += r.gainedSections; totMin += r.gainedMin;
    if (r.sections && r.after.finished >= r.sections) full++;
  });
  console.log("---------------------------------------------");
  console.log(`合计：刷了 ${movedCourses} 门课，${totSec} 节，新增 ${fmtMin(totMin)}，`
    + `总用时 ${((Date.now() - t0) / 60000).toFixed(1)} 分钟`);
  console.log(`      本次共处理 ${touched} 门课，其中 ${full} 门课时已全部刷满`);
})();
