"""Dekoder panelu musi czytać ramki tak jak ebusd. Oczekiwane wartości pochodzą z wstrzyknięcia tych ramek
do ebusd 26.1.26.1 (tools/runall.sh). usage: python3 tools/test_ebuscsv.py"""
import os, sys, unittest
TOOLS = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
from ebuscsv import load_templates, load_lines, Matcher, decode, ebusd_text

LINES = load_lines([os.path.join(ROOT, f) for f in ('ariston.csv', 'ariston_nimbus50s.csv')],
                   load_templates(os.path.join(ROOT, '_templates.csv')))
M = Matcher(LINES)


def read(q, a=''):
    ln = M.match(q)
    return ln and ln.name, ln and ebusd_text(ln, decode(ln, q, a))


class EbusdCompat(unittest.TestCase):
    def test_counter_divisor_in_float32(self):
        # 3291490 / 360 = 9143.0278, ebusd liczy na float32 i wypisuje 9143.027
        self.assertEqual(read('7f1820000452d953d9', '050362393200'), ('ch_electric_energy', '9143.027'))

    def test_broadcast_reads_all_fields_from_master(self):
        # linia ma część 's', ale rozgłoszenie nie ma odpowiedzi
        self.assertEqual(read('70fe202003012001'), ('heating_status', 'on'))

    def test_time_and_date_types(self):
        self.assertEqual(read('70fe2070071b10090a051a11'), ('cast_date', '16:27;09.10.2026'))

    def test_longest_id_wins(self):
        self.assertEqual(read('131e2000027c4f', '03011700'), ('heatpump_power', '23'))

    def test_invalid_bcd_is_an_error(self):
        ln = M.match('131e2000020c45')
        with self.assertRaisesRegex(ValueError, 'out of valid range'):
            decode(ln, '131e2000020c45', '0201b7')

    def test_short_answer_is_invalid_position(self):
        ln = M.match('131e2000086e13ca46cb46d84b')
        with self.assertRaisesRegex(ValueError, 'invalid position'):
            decode(ln, '131e2000086e13ca46cb46d84b', '00')


if __name__ == '__main__':
    unittest.main()
