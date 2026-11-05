import unittest
import pymupdf

from exercise_extractor.extraction import build_section_tree, extract_exercises
from exercise_extractor.textbook_regions import _is_new_part, locate_text_exercise_regions, truncate_to_exercise_heading
from exercise_extractor.toc_detect import is_exercise_title


class TextbookRegionTests(unittest.TestCase):
    def test_chapter_problems_survive_part_boundary_and_appendix_children(self):
        with pymupdf.open() as doc:
            for _ in range(27):
                doc.new_page()
            for page in (7, 11, 17):
                doc[page - 1].insert_text((50, 50), 'PROBLEMS\n1. First problem')
            toc = [
                {'title': 'Part I', 'page_num': None, 'children': [
                    {'title': '1 Earlier', 'page_num': 2},
                    {'title': '2 Numbers', 'page_num': 5},
                ]},
                {'title': 'Part II', 'page_num': None, 'children': [
                    {'title': '3 Functions', 'page_num': 9, 'children': [
                        {'title': 'Appendix. Ordered Pairs', 'page_num': 13},
                    ]},
                    {'title': '4 Graphs', 'page_num': 15, 'children': [
                        {'title': 'Appendix 1. Vectors', 'page_num': 20},
                    ]},
                    {'title': '5 Limits', 'page_num': 23},
                ]},
            ]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            locate_text_exercise_regions(tree, doc)
            chapters = {
                node['title']: node
                for part in tree for node in part.get('children', [])
            }
            for title, start, boundary in (
                ('2 Numbers', 7, 9),
                ('3 Functions', 11, 13),
                ('4 Graphs', 17, 20),
            ):
                children = chapters[title].get('children', [])
                self.assertEqual(len(children), 1, title)
                self.assertEqual(children[0]['exercise_page_range'], [start, boundary + 1])

    def test_next_chapter_at_page_top_excludes_boundary_page_when_ocr_misses_title(self):
        with pymupdf.open() as doc:
            for _ in range(10):
                doc.new_page()
            doc[4].insert_text((50, 50), '5 PROBLEMS\n1. First exercise')
            doc[6].insert_text((50, 50), '42. Last exercise')
            doc[7].insert_text((50, 60), '6 CONTINUOUS FUNCTIONS')
            doc[7].insert_text((50, 180), 'If f is an arbitrary function')
            toc = [{'title': 'Part II', 'page_num': 1, 'children': [
                {'title': '5 Limits', 'page_num': 2},
                {'title': '6 Continuous Functions', 'page_num': 8},
            ]}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            node = tree[0]['children'][0]['children'][0]
            locate_text_exercise_regions(tree, doc)
            self.assertEqual(node['exercise_page_range'], [5, 8])
            node['exercise_content'] = (
                '<!-- page: 5 -->\nPROBLEMS\n\n1. First exercise.\n'
                '<!-- page: 7 -->\n42. Last exercise.\n'
            )
            extract_exercises(tree)
            self.assertNotIn('If f is an arbitrary function', node['exercises'][-1]['content'])

    def test_boundary_page_can_still_contain_end_of_previous_exercises(self):
        with pymupdf.open() as doc:
            for _ in range(10):
                doc.new_page()
            doc[4].insert_text((50, 50), '5 PROBLEMS\n1. First exercise')
            doc[7].insert_text((50, 60), '42. Continued exercise')
            doc[7].insert_text((50, 350), '6 CONTINUOUS FUNCTIONS')
            toc = [{'title': 'Part II', 'page_num': 1, 'children': [
                {'title': '5 Limits', 'page_num': 2},
                {'title': '6 Continuous Functions', 'page_num': 8},
            ]}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            node = tree[0]['children'][0]['children'][0]
            locate_text_exercise_regions(tree, doc)
            self.assertEqual(node['exercise_page_range'], [5, 9])

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

    def test_chapter_problems_heading_expands_tail_and_truncates_prose(self):
        with pymupdf.open() as doc:
            for _ in range(22): doc.new_page()
            doc[10].insert_text((50, 50), 'Chapter discussion\nPROBLEl\\IS\n1. Prove a property.')
            toc = [{'title': 'Part I', 'page_num': 2, 'children': [
                {'title': '1 Basic Properties of Numbers', 'page_num': 3},
                {'title': '2 Numbers of Various Sorts', 'page_num': 20},
            ]}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            node = tree[0]['children'][0]['children'][0]
            self.assertEqual(node['exercise_page_range'], [17, 21])
            locate_text_exercise_regions(tree, doc)
            self.assertEqual(node['exercise_page_range'], [11, 21])
            self.assertEqual(node['_exercise_heading_number'], '1')
            node['exercise_content'] = '<!-- page: 11 -->\nChapter discussion\n\n## PROBLEMS\n\n1. Prove a property.'
            extract_exercises(tree)
            self.assertEqual(len(node['exercises']), 1)
            self.assertNotIn('Chapter discussion', node['exercises'][0]['content'])

    def test_chapter_problems_mentioned_in_prose_do_not_expand_tail(self):
        with pymupdf.open() as doc:
            for _ in range(22): doc.new_page()
            doc[10].insert_text((50, 50), 'These problems are discussed below.\nUse Exercises 1 and 2.')
            toc = [{'title': 'Part I', 'page_num': 2, 'children': [
                {'title': '1 Basic Properties of Numbers', 'page_num': 3},
                {'title': '2 Numbers of Various Sorts', 'page_num': 20},
            ]}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            node = tree[0]['children'][0]['children'][0]
            locate_text_exercise_regions(tree, doc)
            self.assertEqual(node['exercise_page_range'], [17, 21])

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
    def test_section_exercises_share_project_opening_page(self):
        with pymupdf.open() as doc:
            for _ in range(9):
                doc.new_page()
            doc[3].insert_text((50, 50), '2.6 Exercises\n48. Differentiate.')
            # The running header names the project, but the left columns still
            # contain the preceding section's last exercises.
            doc[4].insert_text((340, 35), 'LABORATORY PROJECT FAMILIES OF CURVES')
            doc[4].insert_text((50, 100), '49. A late exercise')
            doc[4].insert_text((50, 500), 'LABORATORY PROJECT CAS FAMILIES OF CURVES')
            toc = [{'title': '2 Derivatives', 'page_num': 1, 'children': [
                {'title': '2.6 Implicit Differentiation', 'page_num': 2,
                 'children': [{'title': 'Laboratory Project • Families of Curves',
                               'page_num': 5}]},
                {'title': '2.7 Rates of Change', 'page_num': 7},
            ]}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            locate_text_exercise_regions(tree, doc)
            section = tree[0]['children'][0]
            self.assertEqual([child['title'] for child in section['children']], [
                '2.6 Implicit Differentiation',
                'Laboratory Project • Families of Curves',
            ])
            exercises, project = section['children']
            project_exercises = project['children'][0]
            self.assertEqual(exercises['exercise_page_range'], [4, 6])
            exercises['exercise_content'] = (
                '<!-- page: 4 -->\n## 2.6 Exercises\n48. Differentiate.\n'
                '<!-- page: 5 -->\n49. A late exercise.\n'
                '## LABORATORY PROJECT CAS FAMILIES OF CURVES\n1. Project task.\n2. Second task.'
            )
            project_exercises['exercise_content'] = (
                '<!-- page: 5 -->\n49. A late exercise.\n'
                '## LABORATORY PROJECT CAS FAMILIES OF CURVES\n1. Project task.\n2. Second task.'
            )
            extract_exercises(tree)
            self.assertEqual([e.get('original_number') for e in exercises['exercises']], ['48', '49'])
            self.assertEqual([e['number'] for e in project_exercises['exercises']], ['1', '2'])

    def test_numbered_section_exercises_before_project_are_retained(self):
        with pymupdf.open() as doc:
            for _ in range(15):
                doc.new_page()
            doc[7].insert_text((50, 50), '4.4 Exercises\n1. Integrate')
            doc[9].insert_text((50, 50), '2. Another integral')
            doc[10].insert_text((50, 50),
                                'WRITING PROJECT\nNEWTON, LEIBNIZ, AND THE INVENTION OF CALCULUS')
            toc = [{'title': '4 Integrals', 'page_num': 2, 'children': [
                {'title': '4.4 Indefinite Integrals', 'page_num': 3,
                 'children': [{'title': 'Writing Project • Newton, Leibniz, and the Invention of Calculus',
                               'page_num': 11}]},
                {'title': '4.5 Substitution', 'page_num': 13},
            ]}]
            tree = build_section_tree(toc, doc_page_count=len(doc))
            locate_text_exercise_regions(tree, doc)
            section = tree[0]['children'][0]
            exercises = [child for child in section['children'] if child.get('implicit')]
            self.assertEqual(len(exercises), 1)
            self.assertEqual(exercises[0]['exercise_page_range'], [8, 11])

    def test_build_sections_keeps_problems_across_parts_and_before_appendix(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from exercise_extractor.ocr import build_sections

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'book.pdf'
            with pymupdf.open() as doc:
                for _ in range(22):
                    doc.new_page()
                doc[6].insert_text((50, 50), 'PROBLEMS\n1. Chapter two problem')
                doc[10].insert_text((50, 50), 'PROBLEMS\n1. Chapter three problem')
                doc[16].insert_text((50, 50), 'PROBLEMS\n1. Chapter four problem')
                doc.save(source)
            toc = [
                {'title': 'Part I', 'page_num': None, 'children': [
                    {'title': '1 Earlier', 'page_num': 2},
                    {'title': '2 Numbers', 'page_num': 5},
                ]},
                {'title': 'Part II', 'page_num': None, 'children': [
                    {'title': '3 Functions', 'page_num': 9, 'children': [
                        {'title': 'Appendix. Ordered Pairs', 'page_num': 13},
                    ]},
                    {'title': '4 Graphs', 'page_num': 15, 'children': [
                        {'title': 'Appendix 1. Vectors', 'page_num': 20},
                    ]},
                ]},
            ]
            args = SimpleNamespace(
                input=str(source), doc_type='textbook', model_dir=tmp, ocr_cache=tmp
            )
            with patch('exercise_extractor.ocr.toc_forge.bookmark_pdf',
                       return_value=SimpleNamespace(toc_tree=toc)):
                sections = build_sections(args)
            chapters = {
                node['title']: node
                for part in sections for node in part.get('children', [])
            }
            self.assertEqual(chapters['2 Numbers']['children'][0]['exercise_page_range'][0], 7)
            self.assertEqual(chapters['3 Functions']['children'][0]['exercise_page_range'][0], 11)
            self.assertEqual(chapters['4 Graphs']['children'][0]['exercise_page_range'][0], 17)

    def test_build_sections_locates_unnumbered_chapter_problems(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from exercise_extractor.ocr import build_sections

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'book.pdf'
            with pymupdf.open() as doc:
                for _ in range(22): doc.new_page()
                doc[10].insert_text((50, 50), 'PROBLEl\\IS\n1. First problem')
                doc.save(source)
            toc = [{'title': 'Part I', 'page_num': 2, 'children': [
                {'title': '1 Basic Properties of Numbers', 'page_num': 3},
                {'title': '2 Numbers of Various Sorts', 'page_num': 20},
            ]}]
            args = SimpleNamespace(input=str(source), doc_type='textbook', model_dir=tmp, ocr_cache=tmp)
            with patch('exercise_extractor.ocr.toc_forge.bookmark_pdf', return_value=SimpleNamespace(toc_tree=toc)):
                sections = build_sections(args)
            node = sections[0]['children'][0]['children'][0]
            self.assertEqual(node['exercise_page_range'], [11, 21])

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
