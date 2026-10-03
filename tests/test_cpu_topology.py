import struct,unittest
from tools.cpu_topology import decode_cpu_sets,homogeneous_cpus

def row(cpu,core,cls):return {'logical_cpu':cpu,'core_index':core,'efficiency_class':cls,'group':0,'allocated':False,'allocated_to_process':False}

class CpuTopologyTests(unittest.TestCase):
    def test_spread_smt_without_mixing_efficiency_classes(self):
        rows=[row(i,i//2,1)for i in range(16)]+[row(i,i,0)for i in range(16,32)]
        cpus,cls=homogeneous_cpus(rows)
        self.assertEqual(cpus,list(range(0,16,2)));self.assertEqual(cls,1)

    def test_request_does_not_silently_mix_classes(self):
        with self.assertRaises(ValueError):homogeneous_cpus([row(i,i,i%2)for i in range(8)],8)

    def test_sixteen_p_threads_include_smt_siblings_on_same_eight_cores(self):
        rows=[row(i,i//2,1)for i in range(16)]+[row(i,i,0)for i in range(16,32)]
        cpus,cls=homogeneous_cpus(rows,16)
        self.assertEqual(set(cpus),set(range(16)));self.assertEqual(cls,1)
        self.assertEqual(len({r['core_index']for r in rows if r['logical_cpu']in cpus}),8)

    def test_variable_records_and_unknown_record_types(self):
        record=struct.pack('<IIIH6B',32,0,100,0,3,2,1,0,1,0)+bytes(12)
        data=struct.pack('<II',8,99)+record
        self.assertEqual(decode_cpu_sets(data)[0]['logical_cpu'],3)
        with self.assertRaises(ValueError):decode_cpu_sets(record[:-1])

if __name__=='__main__':unittest.main()
