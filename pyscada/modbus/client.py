# -*- coding: utf-8 -*-
from pyscada import log
from pyscada.models import ClientWriteTask
from pyscada.models import Client
from pyscada.models import RecordedEvent
from pyscada.models import RecordedTime
from pyscada.models import RecordedDataFloat
from pyscada.models import RecordedDataInt
from pyscada.models import RecordedDataBoolean
from pyscada.models import RecordedDataCache
from pyscada.models import Event
from pyscada.utils import encode_value
from pyscada.utils import get_bits_by_class
from pyscada.utils import decode_value
from pyscada.modbus.utils import decode_address

from django.conf import settings
from pymodbus.client.sync import ModbusTcpClient as ModbusClient
from math import isnan, isinf
from time import time

class CoilBlock:
    def __init__(self):
        self.variable_id            = [] #
        self.variable_address       = [] #
        
    
    def insert_item(self,variable_id,variable_address):
        if not self.variable_address:
            self.variable_address.append(variable_address)
            self.variable_id.append(variable_id)
        elif max(self.variable_address) < variable_address:
            self.variable_address.append(variable_address)
            self.variable_id.append(variable_id)
        elif min(self.variable_address) > variable_address:
            self.variable_address.insert(0,variable_address)
            self.variable_id.insert(0,variable_id)
        else:
            i = self.find_gap(self.variable_address,variable_address)
            if (i is not None):
                self.variable_address.insert(i,variable_address)
                self.variable_id.insert(i,variable_id)
        
    
    
    def request_data(self,slave):
        quantity = len(self.variable_address) # number of bits to read
        first_address = min(self.variable_address)
        
        result = slave.read_coils(first_address,quantity)
        if not hasattr(result, 'bits'):
            return result
            
        return self.decode_data(result)
        
    
    def decode_data(self,result):
        out = {}
        for idx in self.variable_id:
            out[idx] = result.bits.pop(0)
        return out
    
    def find_gap(self,L,value):
        for index in range(len(L)):
            if L[index] == value:
                return None
            if L[index] > value:
                return index

class RegisterBlock:
    def __init__(self):
        self.variable_address   = [] #
        self.variable_length    = [] # in bytes
        self.variable_class     = [] #
        self.variable_id        = [] #
    
    
    def insert_item(self,variable_id,variable_address,variable_class,variable_length):
        if not self.variable_address:
            self.variable_address.append(variable_address)
            self.variable_length.append(variable_length)
            self.variable_class.append(variable_class)
            self.variable_id.append(variable_id)
        elif max(self.variable_address) < variable_address:
            self.variable_address.append(variable_address)
            self.variable_length.append(variable_length)
            self.variable_class.append(variable_class)
            self.variable_id.append(variable_id)
        elif min(self.variable_address) > variable_address:
            self.variable_address.insert(0,variable_address)
            self.variable_length.insert(0,variable_length)
            self.variable_class.insert(0,variable_class)
            self.variable_id.insert(0,variable_id)
        else:
            i = self.find_gap(self.variable_address,variable_address)
            if (i is not None):
                self.variable_address.insert(i,variable_address)
                self.variable_length.insert(i,variable_length)
                self.variable_class.insert(i,variable_class)
                self.variable_id.insert(i,variable_id)
    
    
    def request_data(self,slave):
        quantity = sum(self.variable_length) # number of bits to read
        first_address = min(self.variable_address)
        
        result = slave.read_input_registers(first_address,quantity/16)
        if not hasattr(result, 'registers'):
            return result
    
        return self.decode_data(result)
        
        
    def decode_data(self,result):
        out = {}
        #var_count = 0
        for idx in range(len(self.variable_length)):
            tmp = []
            for i in range(self.variable_length[idx]/16):
                tmp.append(result.registers.pop(0))
            out[self.variable_id[idx]] = decode_value(tmp,self.variable_class[idx])
            if isnan(out[self.variable_id[idx]]) or isinf(out[self.variable_id[idx]]):
                    out[self.variable_id[idx]] = None
        return out
    
    
    def find_gap(self,L,value):
        for index in range(len(L)):
            if L[index] == value:
                return None
            if L[index] > value:
                return index

class client:
    """
    Modbus client (Master) class
    """
    def __init__(self,client):
        self._address               = client.modbusclient.ip_address
        self._port                  = client.modbusclient.port
        self.trans_variable_config  = []
        self.trans_variable_bit_config = []
        self.variables  = {}
        self._variable_config   = self._prepare_variable_config(client)
        
    
    def _prepare_variable_config(self,client):
        
        for var in client.variable_set.filter(active=1):
            if not hasattr(var,'modbusvariable'):
                continue
            Address      = decode_address(var.modbusvariable.address)
            bits_to_read = get_bits_by_class(var.value_class)
            self.variables[var.pk] = {'value_class':var.value_class,'writeable':var.writeable,'record':var.record,'name':var.name}
            if isinstance(Address, list):
                self.trans_variable_bit_config.append([Address,var.pk])
            else:
                self.trans_variable_config.append([Address,var.value_class,bits_to_read,var.pk])
            
    
        self.trans_variable_config.sort()
        self.trans_variable_bit_config.sort()
        out = []
        old = -2
        regcount = 0
        #
        for entry in self.trans_variable_config:
            if (entry[0] != old) or regcount >122:
                regcount = 0
                out.append(RegisterBlock()) # start new register block
            out[-1].insert_item(entry[3],entry[0],entry[1],entry[2]) # add item to block
            old = entry[0] + entry[2]/16
            regcount += entry[2]/16
    
        # bit registers
        old = -2
        for entry in self.trans_variable_bit_config:
            if (entry[0][2] != old+1):
                out.append(CoilBlock()) # start new coil block
            out[-1].insert_item(entry[1],entry[0][2])
            old = entry[0][2]
        return out
    
    
    def _connect(self):
        """
        connect to the modbus slave (server)
        """
        self.slave = ModbusClient(self._address,int(self._port))
        status = self.slave.connect()
        return status
        
    
    
    def _disconnect(self):
        """
        close the connection to the modbus slave (server)
        """
        self.slave.close()
    
    def request_data(self):
        """
    
        """
        data = {};
        if not self._connect():
            return False
        for register_block in self._variable_config:
            result = register_block.request_data(self.slave)
            if result is None:
                self._disconnect()
                self._connect()
                result = register_block.request_data(self.slave)
            
            if result is not None:
                
                data = dict(data.items() + result.items())
                
            else:
                for variable_id in register_block.variable_id:
                    log.error(("variable with id: %d is not accessible")%(variable_id))
                    data[variable_id] = None
            
        self._disconnect()
        return data
    
    def write_data(self,variable_id, value):
        """
        write value to single modbus register or coil
        """
        if not self.variables[variable_id]['writeable']:
            return False
        var_cfg = []
        # find variable config
        for entry in self.trans_variable_config:
            if entry[3] == variable_id:
                var_cfg = entry
                break
        if var_cfg:
            # write register
            if 0 <= var_cfg[0] <= 65535:
                self._connect()
                if var_cfg[2]/16 == 1:
                    # just write the value to one register
                    self.slave.write_register(var_cfg[0],int(value))
                else:
                    # encode it first
                    self.slave.write_registers(var_cfg[0],list(encode_value(value,var_cfg[1])))
                self._disconnect()
                return True
            else:
                log.error('Modbus Address %d out of range'%var_cfg[0])
                return False
        else:
            for entry in self.trans_variable_bit_config:
                if entry[1] == variable_id:
                    var_cfg = entry
                    break
        if var_cfg:
            # write coil
            if 0 <= var_cfg[0][2] <= 65535:
                self._connect()
                self.slave.write_coil(var_cfg[0][2],bool(value))
                self._disconnect()
                return True
            else:
                log.error('Modbus Address %d out of range'%var_cfg[0][2])
        
        return False 

class DataAcquisition:
    def __init__(self):
        self._dt        = float(settings.PYSCADA_MODBUS['polling_interval'])
        self._com_dt    = 0
        self._dvf       = []
        self._dvi       = []
        self._dvb       = []
        self._dvc       = []
        self._clients   = {}
        self.data       = {}
        self._prev_data = {}
        self._prepare_clients()
    
    def _prepare_clients(self):
        """
        prepare clients for query
        """
        for item in Client.objects.filter(active=1):
            if hasattr(item,'modbusclient'):
                self._clients[item.pk] = client(item)
    
    
    def run(self):
        """
            request data
        """
        dt = time()
        ## if there is something to write do it 
        self._do_write_task()
        
        self.data = {}
        # take time
        self.time = time()
        
        for idx in self._clients:
            self.data[idx] = self._clients[idx].request_data()
        
        if not self.data:
            return 
        
        
        self._dvf = []
        self._dvi = []
        self._dvb = []
        self._dvc = []
        del_idx   = []
        upd_idx   = []
        timestamp = RecordedTime(timestamp=self.time)
        timestamp.save()
        for idx in self._clients:
            for var_idx in self._clients[idx].variables:
                store_value = False
                value = 0
                if self.data[idx]:
                    if self.data[idx].has_key(var_idx):
                        if (self.data[idx][var_idx] != None):
                            value = self.data[idx][var_idx]
                            store_value = True
                            if self._prev_data.has_key(var_idx):
                                if value == self._prev_data[var_idx]:
                                    store_value = False
                                    
                            self._prev_data[var_idx] = value
                if store_value:
                    self._dvc.append(RecordedDataCache(variable_id=var_idx,value=value,time=timestamp,last_change = timestamp))
                    del_idx.append(var_idx)
                else:
                    upd_idx.append(var_idx)
                            
                if store_value and self._clients[idx].variables[var_idx]['record']:
                    variable_class = self._clients[idx].variables[var_idx]['value_class']
                    if variable_class.upper() in ['FLOAT','FLOAT64','DOUBLE']:
                        self._dvf.append(RecordedDataFloat(time=timestamp,variable_id=var_idx,value=float(value)))
                    elif variable_class.upper() in ['FLOAT32','SINGLE','REAL'] :
                        self._dvf.append(RecordedDataFloat(time=timestamp,variable_id=var_idx,value=float(value)))
                    elif  variable_class.upper() in ['INT32']:
                        self._dvi.append(RecordedDataInt(time=timestamp,variable_id=var_idx,value=int(value)))
                    elif  variable_class.upper() in ['WORD','UINT','UINT16']:
                        self._dvi.append(RecordedDataInt(time=timestamp,variable_id=var_idx,value=int(value)))
                    elif  variable_class.upper() in ['INT16','INT']:
                        self._dvi.append(RecordedDataInt(time=timestamp,variable_id=var_idx,value=int(value)))
                    elif variable_class.upper() in ['BOOL']:
                        self._dvb.append(RecordedDataBoolean(time=timestamp,variable_id=var_idx,value=bool(value)))
        
        RecordedDataCache.objects.filter(variable_id__in=del_idx).delete()
        RecordedDataCache.objects.filter(variable_id__in=upd_idx).update(time=timestamp)
        RecordedDataCache.objects.bulk_create(self._dvc)
        
        RecordedDataFloat.objects.bulk_create(self._dvf)
        RecordedDataInt.objects.bulk_create(self._dvi)
        RecordedDataBoolean.objects.bulk_create(self._dvb)
        return self._dt -(time()-dt)
    
    
    
    def _do_write_task(self):
        """
        check for write tasks
        """
        
        for task in ClientWriteTask.objects.filter(done=False,start__lte=time(),failed=False):
            
            if self._clients[task.variable.client_id].write_data(task.variable.id,task.value):
                task.done=True
                task.fineshed=time()
                task.save()
                log.notice('changed variable %s (new value %1.6g %s)'%(task.variable.name,task.value,task.variable.unit.description),task.user)
            else:
                task.failed = True
                task.fineshed=time()
                task.save()
                log.error('change of variable %s failed'%(task.variable.name),task.user)
    
    def _do_event_check(self,timestamp):
        
        return False