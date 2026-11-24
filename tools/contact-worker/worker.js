// nadolabs.com 문의 폼 → 대표 메일함으로 보내는 Cloudflare Worker.
// 받는 주소는 비밀값 DEST(Email Routing 의 인증된 받는 주소)에만 둔다. 코드·페이지에 적지 않는다.
// 받는 사람이 고정이라 남용돼도 우리 메일함 밖으로는 메일이 나가지 않는다.
// 배포: tools/contact-worker 에서 `wrangler deploy`, 처음 한 번 `wrangler secret put DEST`.

const FROM = { email: "noreply@nadolabs.com", name: "나도랩스 홈페이지" };
const ORIGINS = ["https://nadolabs.com", "https://www.nadolabs.com"];
const TYPES = ["앱 사용 문의", "오류 신고", "제안", "제휴·사업", "기타"];
const APPS = ["해당 없음", "지금약국", "하루퍼즐", "나도비서"];
const EMAIL_RE = /^[^\s@<>()",;:]+@[^\s@<>()",;:]+\.[^\s@<>()",;:]{2,}$/;
const MIN_MS = 3000; // 페이지를 연 지 3초 안에 보낸 것은 자동 입력으로 본다

function cors(origin) {
  const ok = ORIGINS.includes(origin);
  return {
    "Access-Control-Allow-Origin": ok ? origin : ORIGINS[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}
const json = (o, status, h) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json; charset=utf-8", ...h } });
const clean = (s, max) => String(s ?? "").replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "").trim().slice(0, max);
const oneLine = (s, max) => clean(s, max).replace(/[\r\n]+/g, " ");

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") || "";
    const h = cors(origin);
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: h });
    if (req.method !== "POST") return json({ ok: false, error: "method" }, 405, h);
    if (!ORIGINS.includes(origin)) return json({ ok: false, error: "origin" }, 403, h);
    if ((Number(req.headers.get("Content-Length")) || 0) > 20000) return json({ ok: false, error: "size" }, 413, h);

    let d;
    try { d = await req.json(); } catch { return json({ ok: false, error: "bad_json" }, 400, h); }

    // 자동 입력 걸러내기: 사람 눈에 안 보이는 칸이 채워졌거나 너무 빨리 보냈으면 성공처럼 답하고 버린다
    const elapsed = Date.now() - Number(d.t || 0);
    if (clean(d.website, 200) || !(elapsed >= MIN_MS && elapsed < 864e5)) return json({ ok: true }, 200, h);

    const type = TYPES.includes(d.type) ? d.type : "기타";
    const app = APPS.includes(d.app) ? d.app : "해당 없음";
    const email = oneLine(d.email, 120);
    const name = oneLine(d.name, 40);
    const subject = oneLine(d.subject, 80);
    const body = clean(d.body, 1500);
    if (!EMAIL_RE.test(email)) return json({ ok: false, error: "email" }, 400, h);
    if (!body) return json({ ok: false, error: "body" }, 400, h);
    if (d.consent !== true) return json({ ok: false, error: "consent" }, 400, h);

    const tag = `[${type}${app !== "해당 없음" ? " · " + app : ""}]`;
    const text = [
      body, "", "—",
      `보낸 사람: ${name ? name + " " : ""}<${email}>`,
      `문의 종류: ${type}`, `관련 앱: ${app}`,
      `받은 시각(KST): ${new Date(Date.now() + 9 * 3600e3).toISOString().replace("T", " ").slice(0, 16)}`,
      "이 메일에 답장하면 보낸 사람에게 바로 갑니다.",
    ].join("\n");

    try {
      await env.EMAIL.send({
        from: FROM, to: env.DEST,
        replyTo: name ? { email, name } : email,
        subject: `[nadolabs 문의] ${tag} ${subject || type}`,
        text,
      });
      return json({ ok: true }, 200, h);
    } catch (e) {
      console.log("send failed", e && (e.code || e.message));
      return json({ ok: false, error: "send" }, 502, h);
    }
  },
};
