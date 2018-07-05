"""
desispec.io.util
================

Utility functions for desispec IO.
"""
import os
import astropy.io
import numpy as np
from astropy.table import Table
from desiutil.log import get_logger

from ..util import healpix_degrade_fixed


def iterfiles(root, prefix):
    '''
    Returns iterator over files starting with `prefix` found under `root` dir
    '''
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        for filename in filenames:
            if filename.startswith(prefix):
                yield os.path.join(dirpath, filename)

def header2wave(header):
    """Converts header keywords into a wavelength grid.

    returns wave = CRVAL1 + range(NAXIS1)*CDELT1

    if LOGLAM keyword is present and true/non-zero, returns 10**wave
    """
    w = header['CRVAL1'] + np.arange(header['NAXIS1'])*header['CDELT1']
    if 'LOGLAM' in header and header['LOGLAM']:
        w = 10**w

    return w

def fitsheader(header):
    """Convert header into astropy.io.fits.Header object.

    header can be:
      - None: return a blank Header
      - list of (key, value) or (key, (value,comment)) entries
      - dict d[key] -> value or (value, comment)
      - Header: just return it unchanged

    Returns fits.Header object
    """
    if header is None:
        return astropy.io.fits.Header()

    if isinstance(header, list):
        hdr = astropy.io.fits.Header()
        for key, value in header:
            hdr[key] = value

        return hdr

    if isinstance(header, dict):
        hdr = astropy.io.fits.Header()
        for key, value in header.items():
            hdr[key] = value
        return hdr

    if isinstance(header, astropy.io.fits.Header):
        return header

    raise ValueError("Can't convert {} into fits.Header".format(type(header)))

def native_endian(data):
    """Convert numpy array data to native endianness if needed.

    Returns new array if endianness is swapped, otherwise returns input data

    Context:
    By default, FITS data from astropy.io.fits.getdata() are not Intel
    native endianness and scipy 0.14 sparse matrices have a bug with
    non-native endian data.
    """
    if data.dtype.isnative:
        return data
    else:
        return data.byteswap().newbyteorder()

def add_columns(data, colnames, colvals):
    '''
    Adds extra columns to a data table

    Args:
        data: astropy Table or numpy structured array
        colnames: list of new column names to add
        colvals: list of column values to add;
            each element can be scalar or vector

    Returns:
        new table with extra columns added

    Example:
        fibermap = add_columns(fibermap,
                    ['NIGHT', 'EXPID'], [20102020, np.arange(len(fibermap))])

    Notes:
        This is similar to `numpy.lib.recfunctions.append_fields`, but
        it also accepts astropy Tables as the `data` input, and accepts
        scalar values to expand as entries in `colvals`.
    '''
    if isinstance(data, Table):
        data = data.copy()
        for key, value in zip(colnames, colvals):
            data[key] = value
    else:
        nrows = len(data)
        colvals = list(colvals)  #- copy so that we can modify
        for i, value in enumerate(colvals):
            if np.isscalar(value):
                colvals[i] = np.full(nrows, value, dtype=type(value))
            else:
                assert len(value) == nrows

        data = np.lib.recfunctions.append_fields(data, colnames, colvals, usemask=False)

    return data

def makepath(outfile, filetype=None):
    """Create path to outfile if needed.

    If outfile isn't a string and filetype is set, interpret outfile as
    a tuple of parameters to locate the file via findfile().

    Returns /path/to/outfile

    TODO: maybe a different name?
    """
    from .meta import findfile
    #- if this doesn't look like a filename, interpret outfile as a tuple of
    #- (night, expid, ...) via findfile.  Only works if filetype is set.
    ### if (filetype is not None) and (not isinstance(outfile, (str, unicode))):
    if (filetype is not None) and (not isinstance(outfile, str)):
        outfile = findfile(filetype, *outfile)

    #- Create the path to that file if needed
    path = os.path.normpath(os.path.dirname(outfile))
    if not os.path.exists(path):
        os.makedirs(path)

    return outfile

def write_bintable(filename, data, header=None, comments=None, units=None,
                   extname=None, clobber=False, primary_extname='PRIMARY'):
    """Utility function to write a fits binary table complete with
    comments and units in the FITS header too.  DATA can either be
    dictionary, an Astropy Table, a numpy.recarray or a numpy.ndarray.
    """
    from astropy.table import Table
    from desiutil.io import encode_table

    log = get_logger()

    #- Convert data as needed
    if isinstance(data, (np.recarray, np.ndarray, Table)):
        outdata = encode_table(data, encoding='ascii')
    else:
        outdata = encode_table(_dict2ndarray(data), encoding='ascii')

    # hdu = astropy.io.fits.BinTableHDU(outdata, header=header, name=extname)
    hdu = astropy.io.fits.convenience.table_to_hdu(outdata)
    if extname is not None:
        hdu.header['EXTNAME'] = extname
    else:
        extname = 1

    if header is not None:
        if isinstance(header, astropy.io.fits.header.Header):
            for key, value in header.items():
                comment = header.comments[key]
                hdu.header[key] = (value, comment)
        else:
            hdu.header.update(header)

    #- Write the data and header


    if os.path.isfile(filename) :
        if extname is None :
            #
            # In DESI, we should *always* be setting the extname, so this
            # might never be called.
            #
            if clobber :
                #- overwrite file
                log.debug("overwriting {}".format(filename))
                hdu0 = astropy.io.fits.PrimaryHDU()
                hdu0.header['EXTNAME'] = primary_extname
                hdulist = astropy.io.fits.HDUList([hdu0, hdu])
                hdulist.writeto(filename, overwrite=True, checksum=True)
            else :
                #- append file
                log.debug("adding new HDU to {}".format(filename))
                astropy.io.fits.append(filename, hdu.data, hdu.header, checksum=True)
        else :
            #- we need to open the file and only overwrite the extension
            with astropy.io.fits.open(filename, mode='update') as fx:
                if extname in fx :
                    if not clobber :
                        log.warning("do not modify {} because extname {} exists".format(filename,extname))
                        return
                    #- need replace here
                    log.debug("replacing HDU {} in {}".format(extname,filename))
                    fx[extname]=hdu
                else :
                    log.debug("adding HDU {} to {}".format(extname,filename))
                    fx.append(hdu)
    else :
        log.debug("writing new file {}".format(filename))
        hdu0 = astropy.io.fits.PrimaryHDU()
        hdu0.header['EXTNAME'] = primary_extname
        hdulist = astropy.io.fits.HDUList([hdu0, hdu])
        hdulist.writeto(filename, checksum=True)


    #- TODO:
    #- The following could probably be implemented for efficiently by updating
    #- the outdata Table metadata directly before writing it out.
    #- The following was originally implemented when outdata was a numpy array.

    #- Allow comments and units to be None
    if comments is None:
        comments = dict()
    if units is None:
        units = dict()

    #- Reopen the file to add the comments and units
    fx = astropy.io.fits.open(filename, mode='update')
    hdu = fx[extname]
    for i in range(1,999):
        key = 'TTYPE'+str(i)
        if key not in hdu.header:
            break
        else:
            value = hdu.header[key]
            if value in comments:
                hdu.header[key] = (value, comments[value])
            if value in units:
                hdu.header['TUNIT'+str(i)] = (units[value], value+' units')

    #- Write updated header and close file
    fx.flush()
    fx.close()

def _dict2ndarray(data, columns=None):
    """
    Convert a dictionary of ndarrays into a structured ndarray

    Also works if DATA is an AstroPy Table.

    Args:
        data: input dictionary, each value is an ndarray
        columns: optional list of column names

    Returns:
        structured numpy.ndarray with named columns from input data dictionary

    Notes:
        data[key].shape[0] must be the same for every key
        every entry in columns must be a key of data

    Example
        d = dict(x=np.arange(10), y=np.arange(10)/2)
        nddata = _dict2ndarray(d, columns=['x', 'y'])
    """
    if columns is None:
        columns = list(data.keys())

    dtype = list()
    for key in columns:
        ### dtype.append( (key, data[key].dtype, data[key].shape) )
        if data[key].ndim == 1:
            dtype.append( (key, data[key].dtype) )
        else:
            dtype.append( (key, data[key].dtype, data[key].shape[1:]) )

    nrows = len(data[key])  #- use last column to get length
    xdata = np.empty(nrows, dtype=dtype)

    for key in columns:
        xdata[key] = data[key]

    return xdata


def healpix_subdirectory(nside, pixel):
    """
    Return a fixed directory path for healpix grouped files.

    Given an NSIDE and NESTED pixel index, return a directory
    named after a degraded NSIDE and pixel followed by the
    original nside and pixel.  This function is just to ensure
    that this subdirectory path is always created by the same
    code.

    Args:
        nside (int): a valid NSIDE value.
        pixel (int): the NESTED pixel index.

    Returns (str):
        a path containing the low and high resolution
        directories.

    """
    subdir = str(pixel//100)
    pixdir = str(pixel)
    return os.path.join(subdir, pixdir)

    #- Note: currently nside isn't used, but if we did want to do a strict
    #- superpix grouping, we would need to know nside and do something like:

    # subnside, subpixel = healpix_degrade_fixed(nside, pixel)
    # return os.path.join("{}-{}".format(subnside, subpixel),
    #     "{}-{}".format(nside, pixel))
