import json
import tempfile
import unittest
from pathlib import Path

from exercise_extractor.extraction import combine_section_pages, extract_exercises
from exercise_extractor.textbook_reading_order import ordered_exercise_page


class TextbookReadingOrderTests(unittest.TestCase):
    def test_interleaved_columns_and_number_blocks_restore_question_order(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory, 'page_1')
            page.mkdir()
            (page / 'imgs').mkdir()
            (page / 'imgs' / 'img_in_image_box_100_260_140_290.jpg').write_bytes(b'image')
            (page / 'READY').write_text('ready\n')
            (page / 'page.md').write_text('## 2.6 Exercises\n\n1.\n\n2.\n\n3.\n\n4.\n')
            blocks = [
                ('paragraph_title', '2.6 Exercises', [60, 200, 350, 230]),
                ('text', 'Find each derivative.\n3.', [60, 235, 450, 260]),
                ('text', '1.', [60, 260, 85, 290]),
                ('text', '2.', [280, 260, 305, 290]),
                ('number', '4.', [760, 260, 785, 290]),
                ('text', '5.', [540, 320, 565, 350]),
                ('text', 'Left one.', [90, 260, 240, 290]),
                ('image', '', [100, 260, 140, 290]),
                ('text', 'Left two.', [310, 260, 460, 290]),
                ('text', 'Right one.', [570, 260, 720, 290]),
                ('text', 'Right two.', [790, 260, 940, 290]),
                ('text', 'Right three.', [570, 320, 720, 350]),
            ]
            (page / 'page.json').write_text(json.dumps({
                'input_path': 'sample.pdf', 'page_index': 0, 'page_count': 1,
                'width': 1000, 'height': 1200,
                'model_settings': {
                    'use_doc_preprocessor': False, 'use_layout_detection': True,
                    'use_chart_recognition': False, 'use_seal_recognition': False,
                    'use_ocr_for_image_block': False, 'format_block_content': False,
                    'merge_layout_blocks': True,
                    'markdown_ignore_labels': ['number', 'header', 'footer'],
                },
                'parsing_res_list': [
                    {'block_label': label, 'block_content': text, 'block_bbox': bbox}
                    for label, text, bbox in blocks
                ],
            }))
            node = {
                'title': '2.6 Implicit Differentiation', 'implicit': True,
                'has_exercise': True, '_exercise_heading_number': '2.6',
                'exercise_page_range': [1, 2], 'children': [],
            }
            sections = [node]
            combine_section_pages(sections, directory)
            self.assertIn('img_in_image_box_100_260_140_290.jpg', node['exercise_content'])
            extract_exercises(sections)
            self.assertEqual(
                [item.get('original_number', item['number']) for item in node['exercises']],
                ['1', '2', '3', '4', '5'],
            )
            self.assertIn('Left one.', node['exercises'][0]['content'])
            self.assertIn('Left two.', node['exercises'][1]['content'])
            self.assertIn('Right one.', node['exercises'][2]['content'])
            self.assertIn('Right two.', node['exercises'][3]['content'])
            self.assertIn('Right three.', node['exercises'][4]['content'])

    def test_single_column_keeps_the_original_page(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory, 'page_1')
            page.mkdir()
            (page / 'page.json').write_text(json.dumps({
                'width': 1000,
                'parsing_res_list': [
                    {'block_label': 'paragraph_title', 'block_content': '2.6 Exercises',
                     'block_bbox': [50, 100, 300, 130]},
                    {'block_label': 'text', 'block_content': '1. First',
                     'block_bbox': [50, 200, 300, 230]},
                    {'block_label': 'text', 'block_content': '2. Second',
                     'block_bbox': [50, 250, 300, 280]},
                ],
            }))
            original = '## 2.6 Exercises\n\n1. First\n\n2. Second\n'
            node = {'implicit': True, '_exercise_heading_number': '2.6',
                    'exercise_page_range': [1, 2]}
            self.assertEqual(ordered_exercise_page(directory, 1, node, original), original)


if __name__ == '__main__':
    unittest.main()
