import unittest
import pymupdf

from exercise_extractor.extraction import build_section_tree, extract_exercises
from exercise_extractor.textbook_regions import _is_new_part, locate_text_exercise_regions, truncate_to_exercise_heading
from exercise_extractor.toc_detect import is_exercise_title


class TextbookRegionTests(unittest.TestCase):
    def test_subject_titles_are_not_exercise_collections(self):
        for title in ('4.1 The Area and Distance Problems', '1.4 The Tangent and Velocity Problems', 'Principles of Problem Solving', 'Questioning Assumptions'):
            self.assertFalse(is_exercise_title(title), title)
        for title in ('Chapter 3 Problems', '1.1 Exercises', 'Problems Plus', 'Review Questions', '补充习题', 'Additional Exercises'):
            self.assertTrue(is_exercise_title(title), title)

    def test_text_layer_finds_exercises_before_the_last_three_pages(self):
        with pymupdf.open() as doc:
            for _ in range(12):
                doc.new_page()
            doc[3].insert_text((50, 50), '1.1\nExercises')
            tree = build_section_tree([{'title':'1 Functions', 'page_num':1, 'children':[
                {'title':'1.1 Functions', 'page_num':2},
                {'title':'1.2 Limits', 'page_num':11},
            ]}])
            node = tree[0]['children'][0]['children'][0]
            self.assertEqual(node['exercise_page_range'], [8, 12])
            locate_text_exercise_regions(tree, doc)
            self.assertEqual(node['exercise_page_range'], [4, 12])
            node['exercise_content'] = '<!-- page: 4 -->\n1. An example, not an exercise\n\n## 1.1 Exercises\n1. First exercise.\n2. Second exercise.'
            extract_exercises(tree)
            self.assertEqual(len(node['exercises']), 2)
            self.assertNotIn('An example', node['exercises'][0]['content'])
            self.assertEqual(node['exercises'][0]['pages'][0], 4)

    def test_scanned_pages_and_other_sections_keep_existing_tail_search(self):
        with pymupdf.open() as doc:
            for _ in range(12): doc.new_page()
            doc[3].insert_text((50, 50), '1.2 Exercises\nUse Exercises 47 and 48.')
            node = {'title':'1.1 Functions', 'implicit':True, '_section_start_page':2,
                    'boundary_page':11, 'exercise_page_range':[8,12], 'children':[]}
            locate_text_exercise_regions([node],doc)
            self.assertEqual(node['exercise_page_range'],[8,12])

    def test_heading_without_markdown_prefix_and_split_lines(self):
        text = '<!-- page: 9 -->\nExample text\n1.1\u2003\n\x03Exercises\n1. Find a limit.'
        self.assertEqual(truncate_to_exercise_heading(text,'1.1'), '<!-- page: 9 -->\n1.1\u2003\n\x03Exercises\n1. Find a limit.')

    def test_last_numbered_section_stops_at_appendix(self):
        with pymupdf.open() as doc:
            for _ in range(15): doc.new_page()
            doc[8].insert_text((50, 50), '8.6 EXERCISES\n1. First question')
            doc[9].insert_text((50, 50), '2. Second question')
            doc[10].insert_text((50, 50), 'APPENDIX\nA\nOther material')
            toc = [{'title': 'Chapter 8', 'page_num': 2, 'children': [
                {'title': '8.5 Earlier', 'page_num': 3},
                {'title': '8.6 Curves and Surfaces', 'page_num': 7},
            ]}, {'title': 'Chapter 9 Online', 'page_num': None}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            locate_text_exercise_regions(tree, doc)
            last = tree[0]['children'][-1]['children'][0]
            self.assertEqual(last['exercise_page_range'], [9, 11])
            self.assertEqual(last['_exercise_heading_number'], '8.6')

    def test_last_numbered_section_without_text_heading_is_not_guessed(self):
        with pymupdf.open() as doc:
            for _ in range(15): doc.new_page()
            toc = [{'title': 'Chapter 8', 'page_num': 2, 'children': [
                {'title': '8.6 Curves and Surfaces', 'page_num': 7},
            ]}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            locate_text_exercise_regions(tree, doc)
            self.assertEqual(tree, [])

    def test_new_chapter_header_ends_unbounded_tail(self):
        self.assertFalse(_is_new_part('492\nCHAPTER 8\nThe Geometry of Vector Spaces', '8.6'))
        self.assertTrue(_is_new_part('500\nCHAPTER 9\nNew material', '8.6'))


class TextbookPipelineIntegrationTests(unittest.TestCase):
    def test_build_sections_uses_physical_pages_and_text_exercise_headings(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from exercise_extractor.ocr import build_sections

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'book.pdf'
            with pymupdf.open() as doc:
                for _ in range(16): doc.new_page()
                doc[7].insert_text((50,50),'1.1\nExercises')
                doc.save(source)
            toc = [{'title':'1 Functions', 'page_num':6, 'children':[
                {'title':'1.1 Functions','page_num':7},
                {'title':'1.2 Limits','page_num':15},
            ]}]
            args = SimpleNamespace(input=str(source), doc_type='textbook', model_dir=tmp, ocr_cache=tmp)
            with patch('exercise_extractor.ocr.toc_forge.bookmark_pdf', return_value=SimpleNamespace(toc_tree=toc)):
                sections = build_sections(args)
            node = sections[0]['children'][0]['children'][0]
            self.assertEqual(node['exercise_page_range'],[8,16])
            self.assertEqual(node['_exercise_heading_number'],'1.1')

    def test_build_sections_keeps_last_exercises_before_appendix(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from exercise_extractor.ocr import build_sections

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'book.pdf'
            with pymupdf.open() as doc:
                for _ in range(15): doc.new_page()
                doc[8].insert_text((50, 50), '8.6 EXERCISES\n1. First question')
                doc[9].insert_text((50, 50), '2. Second question')
                doc[10].insert_text((50, 50), 'APPENDIX\nA\nOther material')
                doc.save(source)
            toc = [{'title': 'Chapter 8', 'page_num': 2, 'children': [
                {'title': '8.5 Earlier', 'page_num': 3},
                {'title': '8.6 Curves and Surfaces', 'page_num': 7},
            ]}, {'title': 'Chapter 9 Online', 'page_num': None}]
            args = SimpleNamespace(input=str(source), doc_type='textbook', model_dir=tmp, ocr_cache=tmp)
            with patch('exercise_extractor.ocr.toc_forge.bookmark_pdf', return_value=SimpleNamespace(toc_tree=toc)):
                sections = build_sections(args)
            last = sections[0]['children'][-1]['children'][0]
            self.assertEqual(last['exercise_page_range'], [9, 11])
            self.assertEqual(last['_exercise_heading_number'], '8.6')


if __name__ == '__main__': unittest.main()
