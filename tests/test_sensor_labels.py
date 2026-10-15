import unittest
from macfan.sensor_labels import annotate, describe, overview


def row(key, value=45, source='smc', status='ok'):
    return {'id':source+':'+key, 'name':key, 'key':key, 'source':source,
            'value':value, 'status':status, 'label_verified':False}


class SensorLabelTests(unittest.TestCase):
    def test_m4_points_named_but_not_claimed_as_physical_cores(self):
        point = describe(row('Tp01'), {'chip':'Apple M4'})
        self.assertEqual(point['group'], 'cpu')
        self.assertEqual(point['display_name'], 'CPU 性能区 · 测点 1')
        self.assertFalse(point['label_verified'])
        self.assertEqual(point['label_status'], 'community_mapping')
        self.assertIn('github.com/exelban/stats',point['label_source'])

    def test_unknown_generation_or_key_is_never_inferred_from_prefix(self):
        for chip,key in [('Apple M3','Tp01'),('Apple M5','Te05'),('Apple M4','TPD0'),('Apple M4','TCMb'),('', 'Tm0p')]:
            self.assertEqual(describe(row(key), {'chip':chip})['group'],'unknown')

    def test_m4_gpu_variant_specific_keys_and_case(self):
        self.assertEqual(describe(row('Tg0G'),{'chip':'Apple M4'})['group'],'gpu')
        self.assertEqual(describe(row('Tg0G'),{'chip':'Apple M4 Pro'})['group'],'unknown')
        self.assertEqual(describe(row('Tg1U'),{'chip':'Apple M4 Pro'})['group'],'gpu')
        self.assertEqual(describe(row('Tm0P'),{'chip':'Apple M4'})['group'],'unknown')

    def test_hid_system_names_translated_and_raw_identity_preserved(self):
        original = row('NAND CH0 temp',source='iohid')
        named = annotate([original], {'chip':'Apple M4'})[0]
        self.assertEqual(named['name'],original['name'])
        self.assertEqual(named['id'],original['id'])
        self.assertEqual(named['display_name'],'SSD 闪存 · 通道 0')
        self.assertEqual(named['label_status'],'system_name')
        self.assertEqual(describe(row('PMU2 tdie5',source='iohid'),{})['display_name'],'电源管理 PMU2 · 芯片内部 5')
        self.assertEqual(describe(row('pACC MTR Temp Sensor0',source='iohid'),{})['group'],'cpu')
        self.assertEqual(describe(row('GPU unexpected',source='iohid'),{})['group'],'unknown')

    def test_overview_excludes_unknown_invalid_and_calibration_values(self):
        rows = annotate([row('Tp01',55),row('Tp05',None,status='unverified'),row('TCMz',115.6),
                         row('PMU tdie1',40,'iohid'),row('PMU tcal',100,'iohid')],{'chip':'Apple M4'})
        result = {g['group']:g for g in overview(rows)}
        self.assertEqual(result['cpu']['value'],55)
        self.assertEqual(result['cpu']['valid_count'],1)
        self.assertEqual(result['power']['value'],40)
        self.assertEqual(result['power']['sensor_count'],2)
        self.assertNotIn('unknown',result)
        self.assertEqual(result['cpu']['value_sensor_id'],'smc:Tp01')

    def test_no_valid_sensor_yields_no_number_instead_of_zero(self):
        result = overview(annotate([row('Tp01',None,status='invalid')],{'chip':'Apple M4'}))
        self.assertIsNone(result[0]['value'])
