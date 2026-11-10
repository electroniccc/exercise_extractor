import unittest

from exercise_extractor.parse_md_exercises import split_exercises


class LongHeadingQuestionTests(unittest.TestCase):
    def test_numbered_heading_question_can_span_pages_before_next_number(self):
        content = (
            '### 9.2 达朗贝尔原理\n'
            '###### 9.2.1 用达朗贝尔原理解 6.3.10 题\n'
            + '推导过程\n' * 50
            + '<!-- page: 46 -->\n'
            + '9.2.2 用达朗贝尔原理解 7.4.2 题\n'
            + '第二题解答\n'
        )
        exercises = split_exercises(content, keep_leading=False, check_section_end=False)
        self.assertEqual([item['number'] for item in exercises], ['9.2.1', '9.2.2'])
        self.assertIn('推导过程', exercises[0]['content'])


if __name__ == '__main__':
    unittest.main()
