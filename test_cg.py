#!/usr/bin/env python3
"""Tests hors ligne des parseurs, sur des extraits réels des 3 sites."""
from scraper_cg import parse_cg, parse_sgg, parse_mcrpsc, date_from_arabic, build_email

CG_HTML = """
<div class="row article-format c-gov-img-wrp">
  <a href="/ar/node/13034" class="img col-lg-4"><img alt="x"></a>
  <div class="col-lg-8"><span class="date"><time datetime="2026-09-10T12:00:00Z">الخميس 10 سبتمبر 2026</time></span>
  <h4 class="h4"><a href="/ar/node/13034">اجتماع مجلس الحكومة ليومه الخميس 10 سبتمبر 2026</a></h4>
  <p>انعقد يومه الخميس 28 من ربيع الأول 1448…</p></div></div>
<div class="row article-format c-gov-img-wrp">
  <a href="/ar/node/13007" class="img"><img></a>
  <div><time datetime="2026-07-22T12:00:00Z">الأربعاء 22 يوليوز 2026</time>
  <h4 class="h4"><a href="/ar/node/13007">اجتماع مجلس الحكومة ليومه الأربعاء 22 يوليو 2026</a></h4><p>…</p></div></div>
"""

SGG_HTML = """
<table class="sggTable"><thead><tr><th>تاريخ</th><th>جدول</th><th>بيان</th></tr></thead><tbody>
<tr class="content-table-sgg even"><td><p>10.09.2026</p></td>
  <td><p><p><a href="https://www.sgg.gov.ma/Portals/1/conseil_gouvernement/oj/2026/OJ_CG_10.09.2026.pdf?ver=x">تحميل</a></p></p></td><td><p></p></td></tr>
<tr class="content-table-sgg odd"><td><p>16.07.2026</p></td>
  <td><p><p><a href="https://www.sgg.gov.ma/Portals/1/conseil_gouvernement/oj/2026/OJ_CG_16.7.2026.pdf">تحميل</a></p><p>(أُجِّل ليوم&nbsp; 22/07/2026)</p></p></td>
  <td><p><p><a href="https://www.sgg.gov.ma/Portals/1/conseil_gouvernement/cr/2026/CR_CG_22.07.2026_ar.pdf">تحميل</a></p></p></td></tr>
</tbody></table>
"""

MCRP_HTML = """
<div class="col-md-5"><h5 class="text-left card-title">بلاغ صحفي حول انعقاد اجتماع مجلس الحكومة ليوم الخميس 28 من ربيع الأول 1448 مُوَافِق 10 سبتمبر 2026</h5>
 <div><a href="/اخبار/بلاغ-صحفي-حول-انعقاد-اجتماع-مجلس-الحكومة-10-سبتمبر-2026/"><img></a></div></div>
<div class="col-md-5"><h5 class="text-left card-title">بلاغ صحفي العيون تحتضن المنتدى الوطني العاشر للجمعيات</h5>
 <div><a href="/اخبار/autre/"><img></a></div></div>
"""


def test_cg():
    r = parse_cg(CG_HTML)
    assert [x["date"] for x in r] == ["2026-09-10", "2026-07-22"]
    assert r[0]["url"] == "https://www.cg.gov.ma/ar/node/13034"


def test_sgg_reporte():
    r = parse_sgg(SGG_HTML)
    assert [x["date"] for x in r] == ["2026-09-10", "2026-07-22"]     # 16.07 reporté au 22.07
    assert r[1]["compte_rendu_url"].endswith("CR_CG_22.07.2026_ar.pdf")


def test_mcrpsc_filtre_et_date():
    r = parse_mcrpsc(MCRP_HTML)
    assert len(r) == 1 and r[0]["date"] == "2026-09-10"


def test_dates_arabes():
    assert date_from_arabic("... 7 من صفر 1448 مُوَافِق 22 يوليو 2026") == "2026-07-22"
    assert date_from_arabic("الخميس 21 ماي 2026") == "2026-05-21"


def test_email_panne_totale():
    subject, html = build_email([], {"cg.gov.ma": "ERREUR : 403"}, all_failed=True, baseline=False)
    assert "ERREUR" in subject and "PAS" in html


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
