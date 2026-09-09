import hashlib
import json
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from parse_archive import DOMParser, find_all, Node
from export_text_annotations import annotate, resolve_url
from site_content import annotated_inline, attach_annotations, counted, SITE_URL


class RecoveryTests(unittest.TestCase):
    def test_semantic_spans_and_malformed_links(self):
        parser = DOMParser()
        parser.feed('<p><s>можно</s> нельзя <a href="https://example.org/a">[пруф]<a href="https://example.org/b">[пруф]</a></a></p>')
        text, spans = annotate(parser.root, "https://munchkindb.ru/")
        rendered = annotated_inline(text, 0, spans, set())
        self.assertIn('<del>можно</del> нельзя', rendered)
        self.assertRegex(rendered, r'href="https://example.org/a"[^>]*>\[пруф\]</a><a[^>]*href="https://example.org/b"[^>]*>\[пруф\]</a>')

    def test_url_safety(self):
        self.assertEqual(resolve_url('forums.sjgames.com/showthread.php?t=38716', 'https://munchkindb.ru/card/annihilation'), 'https://forums.sjgames.com/showthread.php?t=38716')
        self.assertEqual(resolve_url('javascript:alert(1)', 'https://example.org'), '')
        self.assertEqual(resolve_url('https://user:password@example.org', 'https://example.org'), '')

    def test_declensions(self):
        self.assertEqual([counted(n, 'набор', 'набора', 'наборов') for n in (1, 2, 11, 21, 114)], ['1 набор', '2 набора', '11 наборов', '21 набор', '114 наборов'])

    def test_annotation_drift_fails(self):
        with self.assertRaises(ValueError):
            attach_annotations({'slug': 'annihilation', 'specials_text': 'modified'}, 'card')


class BuiltSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = ROOT / 'site'
        cls.index = json.loads((cls.site / 'data/search-index.json').read_text())

    def page(self, path):
        return (self.site / path / 'index.html').read_text()

    def test_known_archive_meaning(self):
        self.assertIn('<del>все равно можно</del> больше нельзя', self.page('faq/ruling--награда-за-автоматическое-убийство'))
        self.assertGreaterEqual(self.page('faq/ruling--щиты-не-оружие').count('<del>'), 2)
        self.assertIn('отменённое изменение', self.page('faq/ruling--изменения-правил-кораблейскакуновтачек-от-16092014'))

    def test_exact_proof_regressions(self):
        annihilation = self.page('cards/annihilation')
        self.assertRegex(annihilation, r'href="http://forums.sjgames.com/showthread.php\?t=88425"[^>]*>\[пруф \(читать всю ветку\)\]</a>')
        self.assertRegex(annihilation, r'href="https://forums.sjgames.com/showthread.php\?t=38716"[^>]*>\[пруф\]</a>')
        tape = self.page('cards/duct-tape')
        self.assertIn('t=15039', tape)
        self.assertIn('t=78399', tape)
        self.assertNotIn('class="missing-proof"', tape)

    def test_alternate_names(self):
        by_url = {item['url']: item for item in self.index}
        for card in json.loads((ROOT / 'exports/cards.json').read_text()):
            item = by_url['/cards/' + quote(card['slug'], safe='-._~') + '/']
            for name in (card.get('page') or {}).get('alternate_names', []):
                self.assertIn(name, item['names'])
                self.assertIn(name, item['search'])

    def test_qa_and_epic(self):
        self.assertIn('class="qa-item"', self.page('faq/ruling--faq-сыграть-немедленно'))
        epic = self.page('faq/epic-munchkin')
        self.assertEqual(epic.count('class="qa-item"'), 10)
        for text in ('19-й', '20-й', '2018', 'qa-portals', 'официальных турнирах'):
            self.assertIn(text, epic)
        self.assertIn('id="epic-munchkin"', self.page('faq'))
        self.assertNotIn('>>', self.page('rulings'))

    def test_epic_grouped_toc(self):
        parser = DOMParser()
        parser.feed(self.page('faq/epic-munchkin'))
        toc, = find_all(parser.root, class_name='article-toc')
        self.assertEqual(len(find_all(toc, class_name='article-toc-group')), 3)
        links = find_all(toc, tag='a')
        expected = {'#' + node.attrs['id'] for node in find_all(parser.root, class_name='qa-item')}
        expected.update('#abilities-' + str(n) for n in range(1, 4))
        self.assertEqual(len(links), 13)
        self.assertEqual({link.attrs['href'] for link in links}, expected)

    def test_catalog_complete_and_small(self):
        self.assertLess((self.site / 'cards/index.html').stat().st_size, 60000)
        pages = [self.site / 'cards/index.html'] + list((self.site / 'cards/page').glob('*/index.html'))
        urls = [url for page in pages for url in re.findall(r'<a class="entity-row" href="([^"]+)"', page.read_text())]
        self.assertEqual(len(urls), 4049)
        self.assertEqual(len(set(urls)), 4049)

    def test_sitemap_and_versions(self):
        tree = ET.parse(self.site / 'sitemap.xml')
        urls = [e.text for e in tree.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn(SITE_URL + '/faq/epic-munchkin/', urls)
        self.assertFalse(any('/rulings/' in url or '/search/' in url or '404.html' in url for url in urls))
        for url in urls:
            self.assertTrue((self.site / unquote(url.removeprefix(SITE_URL)).strip('/') / 'index.html').exists(), url)
        version = hashlib.sha256((self.site / 'data/search-index.json').read_bytes()).hexdigest()[:12]
        self.assertIn('search-index.json?v=' + version, self.page('search'))

    def test_no_unlinked_proofs_in_article_text(self):
        def unlinked(node, in_link=False):
            for child in node.children:
                if isinstance(child, str):
                    if not in_link: yield child
                elif isinstance(child, Node):
                    yield from unlinked(child, in_link or child.tag == 'a')
        for folder in ('cards', 'faq'):
            for page in (self.site / folder).rglob('index.html'):
                parser = DOMParser()
                parser.feed(page.read_text())
                for node in find_all(parser.root, class_name='prose'):
                    self.assertNotRegex(''.join(unlinked(node)), r'\[\s*пруф', str(page))

    def test_epic_is_readable_without_pdf(self):
        text = self.page('faq/epic-munchkin')
        self.assertNotRegex(text, r'href="[^"]+\.pdf')
        for name in ('Воин', 'Вор', 'Клирик', 'Волшебник', 'Дварф', 'Хафлинг', 'Эльф', 'Человек', 'Следопыт', 'Кентавр', 'Ящерко'):
            self.assertIn(name, text)


if __name__ == '__main__': unittest.main()
