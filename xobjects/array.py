import numpy as np

from .typeutils import get_a_buffer, Info
from .scalar import Int64

"""
array itemtype d1 d2 ...
array itemtype d1 : ...
array itemtype (d1,1) (d1,0) ...  F contiguos

There 6 kind of arrays from the combination of
    shape: static, dynamic
    item: static, dynamic


Data layout:
    - [size]: if not (static,static)
    - [d0 d1 ...]: dynamic dimensions (dynamic,*)
    - [stride1 stride2 ...] if nd>1
    - [offsets]: if itemtype is not static (*|dynamic)
    - data: array data

Array class:
    - _size
    - _shape: the shape in memory using C ordering
    - _dshape_idx: index of dynamic shapes
    - _order: the ordering of the index in the API
    - _itemtype
    - _is_static_shape
    - _strides
    - _is_static_type

Array instance:
    - _dshape: value of dynamic dimensions
    - _shape: present if dynamic
    - _strides: shape if dynamic
"""


def get_shape_from_array(value):
    if hasattr(value, "shape"):
        return value.shape
    elif hasattr(value, "_shape"):
        return value._shape
    if hasattr(value, "__len__"):
        shape = (len(value),)
        if len(value) > 0:
            shape0 = get_shape_from_array(value[0])
            if shape0 == ():
                return shape
            for i in value[1:]:
                shapei = get_shape_from_array(i)
                if shapei != shape0:
                    raise ValueError(f"{value} not an array")
            return shape + shape0
        else:
            return shape
    else:
        return ()


def get_strides(shape, order, itemsize):
    """
    shape dimension for each index
    order of the dimensions in the memory layout
    - 0 is slowest variation
    return strides for each index

    off=strides[0]*idx[0]+strides[1]*idx[1]+...+strides[n]*idx[n]

    """
    cshape = [shape[io] for io in order]
    cstrides = get_c_strides(cshape, itemsize)
    return tuple(cstrides[order.index(ii)] for ii in range(len(order)))


def get_f_strides(shape, itemsize):
    """
    calculate strides assuming F ordering
    """
    ss = itemsize
    strides = []
    for sh in shape:
        strides.append(ss)
        ss *= sh
    return tuple(strides)


def get_c_strides(shape, itemsize):
    """
    calculate strides assuming C ordering
    """
    ss = itemsize
    strides = []
    for sh in reversed(shape):
        strides.append(ss)
        ss *= sh
    return tuple(reversed(strides))


def iter_index(shape, order):
    """return index in order of data layout"""
    aorder = [order.index(ii) for ii in range(len(order))]
    for ii in np.ndindex(*[shape[io] for io in order]):
        yield tuple((ii[io] for io in aorder))


def mk_order(order, shape):
    if order == "C":
        return list(range(len(shape)))
    elif order == "F":
        return list(range(len(shape) - 1, -1, -1))
    else:
        return order


def get_offset(idx, strides):
    return sum(ii * ss for ii, ss in zip(idx, strides))


class MetaArray(type):
    def __new__(cls, name, bases, data):
        if "_itemtype" in data:  # specialized class
            _itemtype = data["_itemtype"]
            if _itemtype._size is None:
                data["_is_static_type"] = False
            else:
                data["_is_static_type"] = True
            if "_shape" not in data:
                raise ValueError("No shape defined for the Array")
            if "_order" not in data:
                data["_order"] = "C"
            _shape = data["_shape"]
            dshape = []  # find dynamic shapes
            for ii, d in enumerate(_shape):
                if d is None:
                    data["_is_static_shape"] = False
                    dshape.append(ii)
            if len(dshape) > 0:
                data["_is_static_shape"] = False
                data["_dshape_idx"] = dshape
            else:
                data["_is_static_shape"] = True
                data["_order"] = mk_order(data["_order"], _shape)
                if data["_is_static_type"]:
                    data["_strides"] = get_strides(
                        _shape, data["_order"], _itemtype._size
                    )
                else:
                    data["_strides"] = get_strides(_shape, data["_order"], 8)

            if data["_is_static_shape"] and data["_is_static_type"]:
                _size = _itemtype._size
                for d in _shape:
                    _size *= d

            else:
                _size = None
            data["_size"] = _size

        return type.__new__(cls, name, bases, data)

    def __getitem__(cls, shape):
        return Array.mk_arrayclass(cls, shape)


class Array(metaclass=MetaArray):
    @classmethod
    def mk_arrayclass(cls, itemtype, shape):
        if type(shape) in (int, slice):
            shape = (shape,)
        order = list(range(len(shape)))
        nshape = []
        for ii, dd in enumerate(shape):
            if type(dd) is slice:
                nshape.append(dd.start)
                if dd.stop is not None:
                    order[ii] = dd.stop
            else:
                nshape.append(dd)

        if len(shape) <= 3:
            lst = "NMOPQRSTU"
            sshape = []
            ilst = 0
            for d in nshape:
                if d is None:
                    sshape.append(lst[ilst % len(lst)])
                    ilst += 1
                else:
                    sshape.append(str(d))

            suffix = "by".join(sshape)
        else:
            suffix = f"{len(shape)}D"
        name = itemtype.__name__ + "_" + suffix

        data = {
            "_itemtype": itemtype,
            "_shape": tuple(nshape),
            "_order": tuple(order),
        }
        return MetaArray(name, (cls,), data)

    @classmethod
    def _inspect_args(cls, *args):
        if cls._size is not None:
            # static,static array
            if len(args) == 1:
                shape = get_shape_from_array(args[0])
                if shape != cls._shape:
                    raise ValueError(f"shape not valid for {args[0]} ")
            elif len(args) > 1:
                raise ValueError(f"too many arguments")
            return Info(size=cls._size)
        else:
            info = Info()
            offset = 8  # space for size data
            if cls._is_static_shape:
                items = np.prod(cls._shape)
            else:  # complete dimensions
                if not isinstance(args[0], int):  # init with array
                    value = np.array(args[0])
                    shape = value.shape
                    dshape = []
                    for idim, ndim in enumerate(cls._shape):
                        if ndim is None:
                            dshape.append(idim)
                        else:
                            if shape[idem] != ndim:
                                raise ValueError(
                                    "Array: incompatible dimensions"
                                )
                else:
                    shape = []
                    dshape = []  # index of dynamic shapes
                    for ndim in cls._shape:
                        if ndim is None:
                            shape.append(args[len(dshape)])
                            dshape.append(len(shape))
                        else:
                            shape.append(ndim)
                # now we have shape, dshape
                info.shape = shape
                info.dshape = dshape
                offset += len(dshape) * 8  # space for dynamic shapes
                if len(shape) > 1:
                    offset += len(shape) * 8  # space for strides
                info.order = mk_order(shape, cls._order)
                if cls._is_static_itemtype:
                    info.strides = mk_strides(
                        shape, info.order, cls._itemtype._size
                    )
                else:
                    info.strides = mk_strides(shape, info.order, 8)
                items = np.prod(shape)

            if cls._is_static_itemtype:
                offset += items * cls._itemtype._size  # starting of data
                info.size = offset
            else:
                # args must be an array of correct dimensions
                extra = {}
                offsets = np.empty(shape, dtype="int64")
                offset += items * 8
                for idx in iter_index(shape, order):
                    extra[idx] = cls._itemtype._inspect_args(value[idx])
                    offsets[idx] = offset
                    offset += extra[idx].size
                info.extra = extra
                info.size = offset
            return info

    @classmethod
    def _from_buffer(cls, buffer, offset):
        self = object.__new__(cls)
        self._buffer = buffer
        self._offset = offset
        coffset = offset
        if cls._size is None:
            self._size = Int64._from_buffer(self._buffer, coffset)
            coffset += 8
        if not is_static_shape:
            shape = []
            for dd in cls._shape:
                if dd is None:
                    shape.append(Int64._from_buffer(self._buffer, coffset))
                    coffset += 8
                else:
                    shape.append(dd)
            self._shape = shape
            if len(self._shape) > 1:  # getting strides
                # could be computed from shape and order but offset needs to taken
                strides = []
                for i in range(shape):
                    strides.append(Int64._from_buffer(self._buffer, coffset))
                    coffset += 8
            else:
                if is_static_type:
                    strides = (cls._itemtype._size,)
                else:
                    strides = (8,)
            self._strides = strides
        else:
            shape = cls._shape
        if not is_static_type:
            items = prod(shape)
            self._offsets = Int64._array_from_buffer(buffer, coffset, items)
        return self

    @classmethod
    def _to_buffer(cls, buffer, offset, value, info=None):
        if info is None:
            info = cls._inspect_args(value)
        header = []
        coffset = offset
        if cls._size is None:
            header.append(info.size)
        if not cls._is_static_shape:
            for ii, nd in enumerate(cls._shape):
                if nd is None:
                    header.append(info.shape[ii])
            if len(cls._shape) > 1:
                header.extend(info.strides)
        if len(header) > 0:
            Int64._array_to_buffer(
                buffer, coffset, np.array(header, dtype="i8")
            )
            coffset += 8 * len(header)
        if not cls._is_static_type:
            Int64._array_to_buffer(buffer, coffset, info.offsets)
        if isinstance(value, np.ndarray) and hasattr(
            cls._itemtype, "_dtype"
        ):  # not robust try scalar classes
            if cls._itemtype._dtype == value.dtype:
                buffer.write(value.tobytes())
            else:
                buffer.write(value.astype(cls._itemtype._dtype).tobytes())
        else:
            for idx in iter_index(info.shape, cls._order):
                cls._itemtype._to_buffer(
                    buffer,
                    offset + info.offsets[idx],
                    value[idx],
                    info.extra.get(idx),
                )

    def __init__(self, *args, _context=None, _buffer=None, _offset=None):
        # determin resources
        info = self.__class__._inspect_args(*args)

        self._buffer, self._offset = get_a_buffer(_context, _buffer, _offset)

        if info.value is not None:
            self.__class__._to_buffer(
                self._buffer, self._offset, info.value, info
            )

        if hasattr(info, "size"):
            self._size = info.size
        if hasattr(info, "shape"):
            self._shape = info.shape
            self._dshape = info.dshape
            self._strides = info.strides
        if hasattr(info, "offsets"):
            self._offsets = info.offsets

    def _get_size(self):
        if self.__class__._size is None:
            return Int64._from_buffer(self._buffer, self._offset)
        else:
            return self.__class__._size

    @classmethod
    def _get_offset(cls, index):
        return get_offset(index, cls._strides)

    @classmethod
    def _get_position(cls, index):
        offset = get_offset(index, cls._strides)
        if cls._is_static_type:
            return offset // 8
        else:
            return offset // cls._itemtype._size

    def __getitem__(self, index):
        if isinstance(index, (int, np.integer)):
            index = (index,)
        cls = self.__class__
        if hasattr(self, "_offsets"):
            offset = self._offset + self._offsets[index]
        else:
            offset = self._offset + cls._data_offset + cls.get_offset(index)
        return cls._itemtype._frombuffer(self._buffer, self._offset)

    def __setitem__(self, index, value):
        if isinstance(index, (int, np.integer)):
            index = (index,)
        cls = self.__class__
        if hasattr(cls._itemtype, "_update"):
            self[index]._update(value)
        else:
            if hasattr(self, "_offsets"):
                offset = self._offset + self._offsets[cls.get_position(index)]
            else:
                offset = (
                    self._offset + cls._data_offset + cls.get_offset(index)
                )
            cls._itemtype._to_buffer(instance._buffer, offset, value)

    @classmethod
    def _gen_method_specs(cls, base=None):
        methods = []
        if base is None:
            base = []
        spec = base + [cls]
        methods.append(spec)
        if hasattr(cls._itemtype, "_gen_method_specs"):
            methods.extend(cls._itemtype._gen_method_specs(spec))
        return methods

    @classmethod
    def _get_offset(cls):
        return "+".join(["i{ii}*{strides[ii]}" for ii in cls.strides])

    @classmethod
    def _get_c_offset(cls, conf):
        itype = conf.get("itype", "int64_t")
        if cls._is_static_shape:
            soffset = "+".join(
                [
                    "i{cls._order[ii]}*{ss}"
                    for ii, ss in enumerate(cls._strides)
                ]
            )
            if cls._size != None:
                return f"offset+{soffset}"
            else:  # cls._is_static_type ==False
                doffset = f"offset+8+{soffset}"  # starts of the offset list
                return f"(({itype}*) obj)[{doffset}]"
        else:  # dynamic shape
            strides = []
            sizeoff = 8  # size offset
            for dd in cls._shape:  # WRONG!!!
                if type(dd) is int:
                    strides.append(str(int))
                else:
                    strides.append(f"(({itype}*) obj)[offset+{sizeoff}]")
                    sizeoff += 8
            soffset = "+".join(
                [f"i{cls._order[ii]}*{ss}" for ii, ss in enumerate(strides)]
            )
            off = 8 + len(cls._dshape_idx) * 8
            if cls._is_static_type:
                return f"offset+{off}+{soffset}"
            else:  # dynamic typr:
                return f"offset+(({itype}*) obj)[offset+{off}+{soffset}]"
