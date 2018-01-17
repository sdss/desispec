"""
desispec.sky
============

Utility functions to compute a sky model and subtract it.
"""


import numpy as np
from desispec.resolution import Resolution
from desispec.linalg import cholesky_solve
from desispec.linalg import cholesky_solve_and_invert
from desispec.linalg import spline_fit
from desiutil.log import get_logger
from desispec import util
from desiutil import stats as dustat
import scipy,scipy.sparse,scipy.stats,scipy.ndimage
import sys

def compute_sky(frame, nsig_clipping=4.,max_iterations=100,model_ivar=False,add_variance=True) :
    """Compute a sky model.
    
    Input has to correspond to sky fibers only.
    Input flux are expected to be flatfielded!
    We don't check this in this routine.

    Args:
        frame : Frame object, which includes attributes
          - wave : 1D wavelength grid in Angstroms
          - flux : 2D flux[nspec, nwave] density
          - ivar : 2D inverse variance of flux
          - mask : 2D inverse mask flux (0=good)
          - resolution_data : 3D[nspec, ndiag, nwave]  (only sky fibers)
        nsig_clipping : [optional] sigma clipping value for outlier rejection

    Optional:
        max_iterations : int , number of iterations
        model_ivar : replace ivar by a model to avoid bias due to correlated flux and ivar. this has a negligible effect on sims.
    
    returns SkyModel object with attributes wave, flux, ivar, mask
    """

    log=get_logger()
    log.info("starting")

    # Grab sky fibers on this frame
    skyfibers = np.where(frame.fibermap['OBJTYPE'] == 'SKY')[0]
    assert np.max(skyfibers) < 500  #- indices, not fiber numbers

    nwave=frame.nwave
    nfibers=len(skyfibers)

    current_ivar=frame.ivar[skyfibers].copy()*(frame.mask[skyfibers]==0)
    flux = frame.flux[skyfibers]
    Rsky = frame.R[skyfibers]
    
    input_ivar=None 
    if model_ivar :
        log.info("use a model of the inverse variance to remove bias due to correlated ivar and flux")
        input_ivar=current_ivar.copy()
        median_ivar_vs_wave  = np.median(current_ivar,axis=0)
        median_ivar_vs_fiber = np.median(current_ivar,axis=1)
        median_median_ivar   = np.median(median_ivar_vs_fiber)
        for f in range(current_ivar.shape[0]) :
            threshold=0.01
            current_ivar[f] = median_ivar_vs_fiber[f]/median_median_ivar * median_ivar_vs_wave
            # keep input ivar for very low weights
            ii=(input_ivar[f]<=(threshold*median_ivar_vs_wave))
            #log.info("fiber {} keep {}/{} original ivars".format(f,np.sum(ii),current_ivar.shape[1]))                      
            current_ivar[f][ii] = input_ivar[f][ii]
    

    sqrtw=np.sqrt(current_ivar)
    sqrtwflux=sqrtw*flux

    chi2=np.zeros(flux.shape)

    #debug
    #nfibers=min(nfibers,2)

    nout_tot=0
    for iteration in range(max_iterations) :

        A=scipy.sparse.lil_matrix((nwave,nwave)).tocsr()
        B=np.zeros((nwave))
        # diagonal sparse matrix with content = sqrt(ivar)*flat of a given fiber
        SD=scipy.sparse.lil_matrix((nwave,nwave))
        # loop on fiber to handle resolution
        for fiber in range(nfibers) :
            if fiber%10==0 :
                log.info("iter %d fiber %d"%(iteration,fiber))
            R = Rsky[fiber]

            # diagonal sparse matrix with content = sqrt(ivar)
            SD.setdiag(sqrtw[fiber])

            sqrtwR = SD*R # each row r of R is multiplied by sqrtw[r]

            A = A+(sqrtwR.T*sqrtwR).tocsr()
            B += sqrtwR.T*sqrtwflux[fiber]

        log.info("iter %d solving"%iteration)

        w = A.diagonal()>0
        A_pos_def = A.todense()[w,:]
        A_pos_def = A_pos_def[:,w]
        skyflux = B*0
        try:
            skyflux[w]=cholesky_solve(A_pos_def,B[w])
        except:
            log.info("cholesky failed, trying svd in iteration {}".format(iteration))
            skyflux[w]=np.linalg.lstsq(A_pos_def,B[w])[0]

        log.info("iter %d compute chi2"%iteration)

        for fiber in range(nfibers) :

            S = Rsky[fiber].dot(skyflux)
            chi2[fiber]=current_ivar[fiber]*(flux[fiber]-S)**2

        log.info("rejecting")

        nout_iter=0
        if iteration<1 :
            # only remove worst outlier per wave
            # apply rejection iteratively, only one entry per wave among fibers
            # find waves with outlier (fastest way)
            nout_per_wave=np.sum(chi2>nsig_clipping**2,axis=0)
            selection=np.where(nout_per_wave>0)[0]
            for i in selection :
                worst_entry=np.argmax(chi2[:,i])
                current_ivar[worst_entry,i]=0
                sqrtw[worst_entry,i]=0
                sqrtwflux[worst_entry,i]=0
                nout_iter += 1

        else :
            # remove all of them at once
            bad=(chi2>nsig_clipping**2)
            current_ivar *= (bad==0)
            sqrtw *= (bad==0)
            sqrtwflux *= (bad==0)
            nout_iter += np.sum(bad)

        nout_tot += nout_iter

        sum_chi2=float(np.sum(chi2))
        ndf=int(np.sum(chi2>0)-nwave)
        chi2pdf=0.
        if ndf>0 :
            chi2pdf=sum_chi2/ndf
        log.info("iter #%d chi2=%f ndf=%d chi2pdf=%f nout=%d"%(iteration,sum_chi2,ndf,chi2pdf,nout_iter))

        if nout_iter == 0 :
            break

    log.info("nout tot=%d"%nout_tot)

    # no need restore original ivar to compute model error when modeling ivar
    # the sky inverse variances are very similar

    # solve once again to get deconvolved sky variance
    try :
        unused_skyflux,skycovar=cholesky_solve_and_invert(A.todense(),B)
    except np.linalg.linalg.LinAlgError :
        log.warning("cholesky_solve_and_invert failed, switching to np.linalg.lstsq and np.linalg.pinv")
        #skyflux = np.linalg.lstsq(A.todense(),B)[0]
        skycovar = np.linalg.pinv(A.todense())

    #- sky inverse variance, but incomplete and not needed anyway
    # skyvar=np.diagonal(skycovar)
    # skyivar=(skyvar>0)/(skyvar+(skyvar==0))

    # Use diagonal of skycovar convolved with mean resolution of all fibers
    # first compute average resolution
    mean_res_data=np.mean(frame.resolution_data,axis=0)
    R = Resolution(mean_res_data)
    # compute convolved sky and ivar
    cskycovar=R.dot(skycovar).dot(R.T.todense())
    cskyvar=np.diagonal(cskycovar)
    cskyivar=(cskyvar>0)/(cskyvar+(cskyvar==0))

    # convert cskyivar to 2D; today it is the same for all spectra,
    # but that may not be the case in the future
    cskyivar = np.tile(cskyivar, frame.nspec).reshape(frame.nspec, nwave)

    # Convolved sky
    cskyflux = np.zeros(frame.flux.shape)
    for i in range(frame.nspec):
        cskyflux[i] = frame.R[i].dot(skyflux)
    
    
    
    # look at chi2 per wavelength and increase sky variance to reach chi2/ndf=1
    if skyfibers.size > 1 and add_variance :
        log.info("Add a model error due to wavelength solution noise")
        
        tivar = util.combine_ivar(frame.ivar[skyfibers], cskyivar[skyfibers])
        
        # the chi2 at a given wavelength can be large because on a cosmic
        # and not a psf error or sky non uniformity
        # so we need to consider only waves for which 
        # a reasonable sky model error can be computed
        
        # mean sky
        msky = np.mean(cskyflux,axis=0)
        dwave = np.mean(np.gradient(frame.wave))
        dskydw = np.zeros(msky.shape)
        dskydw[1:-1]=(msky[2:]-msky[:-2])/(frame.wave[2:]-frame.wave[:-2])
        dskydw = np.abs(dskydw)
        
        # now we consider a worst possible sky model error (20% error on flat, 0.5A )
        max_possible_var = 1./(tivar+(tivar==0)) + (0.2*msky)**2 + (0.5*dskydw)**2
        
        # exclude residuals inconsistent with this max possible variance (at 3 sigma)
        bad = (frame.flux[skyfibers]-cskyflux[skyfibers])**2 > 3**2*max_possible_var
        tivar[bad]=0
        ndata = np.sum(tivar>0,axis=0)
        ok=np.where(ndata>1)[0]
        print("ok.size=",ok.size)
        chi2  = np.zeros(frame.wave.size)
        chi2[ok] = np.sum(tivar*(frame.flux[skyfibers]-cskyflux[skyfibers])**2,axis=0)[ok]/(ndata[ok]-1)
        chi2[ndata<=1] = 1. # default
        
        # now we are going to evaluate a sky model error based on this chi2, 
        # but only around sky flux peaks (>0.1*max)
        tmp   = np.zeros(frame.wave.size)
        tmp   = (msky[1:-1]>msky[2:])*(msky[1:-1]>msky[:-2])*(msky[1:-1]>0.1*np.max(msky))
        peaks = np.where(tmp)[0]+1
        dpix  = int(np.ceil(3/dwave)) # +- n Angstrom around each peak
        
        skyvar = 1./(cskyivar+(cskyivar==0))
        
        # loop on peaks
        for peak in peaks :
            b=peak-dpix
            e=peak+dpix+1
            mchi2  = np.mean(chi2[b:e]) # mean reduced chi2 around peak
            mndata = np.mean(ndata[b:e]) # mean number of fibers contributing
                            
            # sky model variance = sigma_flat * msky  + sigma_wave * dmskydw
            sigma_flat=0.000 # the fiber flat error is already included in the flux ivar
            sigma_wave=0.005 # A, minimum value                
            res2=(frame.flux[skyfibers,b:e]-cskyflux[skyfibers,b:e])**2
            var=1./(tivar[:,b:e]+(tivar[:,b:e]==0))
            nd=np.sum(tivar[:,b:e]>0)
            while(sigma_wave<2) :
                pivar=1./(var+(sigma_flat*msky[b:e])**2+(sigma_wave*dskydw[b:e])**2)
                pchi2=np.sum(pivar*res2)/nd
                if pchi2<=1 :
                    log.info("peak at {}A : sigma_wave={}".format(int(frame.wave[peak]),sigma_wave))
                    skyvar[:,b:e] += ( (sigma_flat*msky[b:e])**2 + (sigma_wave*dskydw[b:e])**2 )
                    break
                sigma_wave += 0.005
        
        modified_cskyivar = (cskyivar>0)/skyvar
    else :
        modified_cskyivar = cskyivar.copy()
    
    # need to do better here
    mask = (cskyivar==0).astype(np.uint32)
    
    return SkyModel(frame.wave.copy(), cskyflux, modified_cskyivar, mask,
                    nrej=nout_tot, stat_ivar = cskyivar) # keep a record of the statistical ivar for QA

class SkyModel(object):
    def __init__(self, wave, flux, ivar, mask, header=None, nrej=0, stat_ivar=None):
        """Create SkyModel object

        Args:
            wave  : 1D[nwave] wavelength in Angstroms
            flux  : 2D[nspec, nwave] sky model to subtract
            ivar  : 2D[nspec, nwave] inverse variance of the sky model
            mask  : 2D[nspec, nwave] 0=ok or >0 if problems; 32-bit
            header : (optional) header from FITS file HDU0
            nrej : (optional) Number of rejected pixels in fit

        All input arguments become attributes
        """
        assert wave.ndim == 1
        assert flux.ndim == 2
        assert ivar.shape == flux.shape
        assert mask.shape == flux.shape

        self.nspec, self.nwave = flux.shape
        self.wave = wave
        self.flux = flux
        self.ivar = ivar
        self.mask = util.mask32(mask)
        self.header = header
        self.nrej = nrej
        self.stat_ivar = stat_ivar

def subtract_sky(frame, skymodel) :
    """Subtract skymodel from frame, altering frame.flux, .ivar, and .mask

    Args:
        frame : desispec.Frame object
        skymodel : desispec.SkyModel object
    """
    assert frame.nspec == skymodel.nspec
    assert frame.nwave == skymodel.nwave

    log=get_logger()
    log.info("starting")

    # check same wavelength, die if not the case
    if not np.allclose(frame.wave, skymodel.wave):
        message = "frame and sky not on same wavelength grid"
        log.error(message)
        raise ValueError(message)

    frame.flux -= skymodel.flux
    frame.ivar = util.combine_ivar(frame.ivar, skymodel.ivar)
    frame.mask |= skymodel.mask

    log.info("done")


def qa_skysub(param, frame, skymodel, quick_look=False):
    """Calculate QA on SkySubtraction

    Note: Pixels rejected in generating the SkyModel (as above), are
    not rejected in the stats calculated here.  Would need to carry
    along current_ivar to do so.

    Args:
        param : dict of QA parameters : see qa_frame.init_skysub for example
        frame : desispec.Frame object;  Should have been flat fielded
        skymodel : desispec.SkyModel object
        quick_look : bool, optional
          If True, do QuickLook specific QA (or avoid some)
    Returns:
        qadict: dict of QA outputs
          Need to record simple Python objects for yaml (str, float, int)
    """
    from desispec.qa import qalib
    import copy

    #- QAs
    #- first subtract sky to get the sky subtracted frame. This is only for QA. Pipeline does it separately.
    tempframe=copy.deepcopy(frame) #- make a copy so as to propagate frame unaffected so that downstream pipeline uses it.
    subtract_sky(tempframe,skymodel) #- Note: sky subtract is done to get residuals. As part of pipeline it is done in fluxcalib stage

    # Sky residuals first
    qadict = qalib.sky_resid(param, tempframe, skymodel, quick_look=quick_look)

    # Sky continuum
    if not quick_look:  # Sky continuum is measured after flat fielding in QuickLook
        channel = frame.meta['CAMERA'][0]
        wrange1, wrange2 = param[channel.upper()+'_CONT']
        skyfiber, contfiberlow, contfiberhigh, meancontfiber, skycont = qalib.sky_continuum(frame,wrange1,wrange2)
        qadict["SKYFIBERID"] = skyfiber.tolist()
        qadict["SKYCONT"] = skycont
        qadict["SKYCONT_FIBER"] = meancontfiber

    if quick_look:  # The following can be a *large* dict
        qadict_snr = qalib.SignalVsNoise(tempframe,param)
        qadict.update(qadict_snr)

    return qadict
