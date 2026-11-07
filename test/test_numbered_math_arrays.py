import unittest

from exercise_extractor.parse_md_exercises import split_exercises


class NumberedMathArrayTests(unittest.TestCase):
    def test_numbered_array_rows_become_separate_exercises(self):
        markdown = r'''### 2.9 Exercises

1–4 Find the linearization of the function at a.

$$ \begin{array}{r l r l}{\mathbf{1}.}&{f(x)=x^{4}+3x^{2},}&{a=-1}&&{\mathbf{2}.}&{f(x)=\sin x,}&{a=\pi/6}\end{array} $$

$$ \begin{array}{r l r l}{3.~f(x)=\sqrt{x},}&{a=4}&&{4.~f(x)=x^{3/4},}&{a=16}\end{array} $$

5. Another question.'''
        exercises = split_exercises(markdown, keep_leading=False)
        self.assertEqual([item['number'] for item in exercises], ['1', '2', '3', '4', '5'])
        self.assertIn('x^{4}+3x^{2}', exercises[0]['content'])
        self.assertIn(r'a=\pi/6', exercises[1]['content'])
        self.assertIn('a=4', exercises[2]['content'])
        self.assertIn('a=16', exercises[3]['content'])
        self.assertIn('Find the linearization', exercises[0]['content'])

    def test_ordinary_math_array_is_preserved(self):
        markdown = r'''1. Calculate $$ \begin{array}{cc}a&b\\c&d\end{array} $$.'''
        exercises = split_exercises(markdown)
        self.assertEqual(len(exercises), 1)
        self.assertIn(r'\begin{array}', exercises[0]['content'])

    def test_numbered_laws_in_prose_are_not_exercises(self):
        markdown = (r'3 Laws of Logarithms' '\n\n'
                    r'$$ \begin{array}{rlrl}1.&\ln(xy)=\ln x+\ln y&2.&\ln(x/y)=\ln x-\ln y\end{array} $$')
        self.assertEqual(split_exercises(markdown, keep_leading=False), [])

    def test_unrelated_range_does_not_expand_array(self):
        markdown = (r'1–4 Find the derivatives.' '\n\n'
                    r'$$ \begin{array}{rlrl}5.&a&6.&b\end{array} $$' '\n\n'
                    '7. Next question.')
        exercises = split_exercises(markdown, keep_leading=False)
        self.assertEqual([item['number'] for item in exercises], ['7'])

    def test_numbered_array_without_range_is_preserved(self):
        markdown = (r'1. State the numbered identities:' '\n\n'
                    r'$$ \begin{array}{rlrl}1.&a=b&2.&c=d\end{array} $$' '\n\n'
                    '2. Next question.')
        exercises = split_exercises(markdown, keep_leading=False)
        self.assertEqual([item['number'] for item in exercises], ['1', '2'])
        self.assertIn(r'\begin{array}', exercises[0]['content'])
