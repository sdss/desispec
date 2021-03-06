"""
boxcar extraction for Spectra from Desi Image
"""
from __future__ import absolute_import, division, print_function
import numpy as np
from lvmspec.quicklook.palib import resample_spec,get_resolution

def do_boxcar(image,psf,outwave,boxwidth=2.5,nspec=500,maskFile=None,usesigma=False,
              quick_resolution=False):
    """Extracts spectra row by row, given the centroids

    Args:
        image  : lvmspec.image object
        psf: lvmspec.psf.PSF like object
            Or do we just parse the traces here and write a separate wrapper to handle this? Leaving psf in the input argument now.
        outwave: wavelength array for the final spectra output
        boxwidth: HW box size in pixels
        usesigma: if True, use sigma from psfboot file(xsigma) or psf file (wsigma) to calculate resolution data.
        quick_resolution:  whether to calculate the resolution matrix or use QuickResolution object
    Returns flux, ivar, resolution
    """
    import math
    from lvmspec.frame import Frame

    #wavelength=psf.wavelength() # (nspec,npix_y)
    def calcMask(psf):
        wmin=psf.wmin
        wmax=psf.wmax
        waves=np.arange(wmin,wmax,0.25)
        xs=psf.x(None,waves) #- xtraces # doing the full image here.
        ys=psf.y(None,waves) #- ytraces

        camera=image.camera
        spectrograph=int(camera[1:]) #- first char is "r", "b", or "z"
        imshape=image.pix.shape
        mask=np.zeros((imshape[1],imshape[0]))
        maxx,maxy=mask.shape
        maxx=maxx-1
        maxy=maxy-1
        ranges=np.zeros((mask.shape[1],xs.shape[0]+1),dtype=int)
        for bin in range(0,len(waves)):
            ixmaxold=0
            for spec in range(0,xs.shape[0]):
                xpos=xs[spec][bin]
                ypos=int(ys[spec][bin])
                if xpos<0 or xpos>maxx or ypos<0 or ypos>maxy :
                    continue
                xmin=xpos-boxwidth
                xmax=xpos+boxwidth
                ixmin=int(math.floor(xmin))
                ixmax=int(math.floor(xmax))
                if ixmin <= ixmaxold:
                    print("Error Box width overlaps,",xpos,ypos,ixmin,ixmaxold)
                    return None,None
                ixmaxold=ixmax
                if mask[int(xpos)][ypos]>0 :
                    continue
            # boxing in x vals
                if ixmin < 0: #int value is less than 0
                    ixmin=0
                    rxmin=1.0
                else:# take part of the bin depending on real xmin
                    rxmin=1.0-xmin+ixmin
                if ixmax>maxx:# xmax is bigger than the image
                    ixmax=maxx
                    rxmax=1.0
                else: # take the part of the bin depending on real xmax
                    rxmax=xmax-ixmax
                ranges[ypos][spec+1]=math.ceil(xmax)#end at next column
                if  ranges[ypos][spec]==0:
                    ranges[ypos][spec]=ixmin
                mask[ixmin][ypos]=rxmin
                for x in range(ixmin+1,ixmax): mask[x][ypos]=1.0
                mask[ixmax][ypos]=rxmax
        for ypos in range(ranges.shape[0]):
            lastval=ranges[ypos][0]
            for sp in range(1,ranges.shape[1]):
                if  ranges[ypos][sp]==0:
                    ranges[ypos][sp]=lastval
                lastval=ranges[ypos][sp]
        return mask,ranges

    if maskFile is not None:
        import os
        if os.path.exists(maskFile) and os.path.isfile(maskFile):
            f=open(maskFile,'rb')
            npf=np.load(f)
            mask=npf['mask']
            ranges=npf['ranges']
            print("Loading mask from file %s"%maskFile)

        else:
            print("Mask file is given but doesn't exist. Generating mask and saving to file %s"%maskFile)
            mask,ranges=calcMask(psf)
            try:
                f=open(maskFile,'wb')
                np.savez(f,mask=mask,ranges=ranges)
            except:
                pass
    else:
        mask,ranges=calcMask(psf)
    Tmask=mask.T
    maskedimg=(image.pix*Tmask)
    maskedvar=(Tmask/image.ivar.clip(0))

    flux=np.zeros((maskedimg.shape[0],ranges.shape[1]-1))
    ivar=np.zeros((maskedimg.shape[0],ranges.shape[1]-1))

    for r in range(flux.shape[0]):
        row=np.add.reduceat(maskedimg[r],ranges[r])[:-1]
        flux[r]=row
        vrow=np.add.reduceat(maskedvar[r],ranges[r])[:-1]
        ivar[r]=1/vrow

    wtarget=outwave
    #- limit nspec to psf.nspec max
    if nspec > psf.nspec:
        nspec=psf.nspec
        print("Warning! Extracting only {} spectra".format(psf.nspec))

    fflux=np.zeros((nspec,len(wtarget)))
    iivar=np.zeros((nspec,len(wtarget)))

    #- convert to per angstrom first and then resample to desired wave length grid.

    for spec in range(nspec):
        ww=psf.wavelength(spec)
        dwave=np.gradient(ww)
        flux[:,spec]/=dwave
        ivar[:,spec]*=dwave**2
        fflux[spec,:],iivar[spec,:]=resample_spec(ww,flux[:,spec],wtarget,ivar[:,spec])

    #- Get resolution from the psf
    if quick_resolution and  (hasattr(psf,"wcoeff") or hasattr(psf,'xsigma_boot')):
        return fflux,iivar,None
    resolution=get_resolution(wtarget,nspec,psf,usesigma=usesigma)

    return fflux,iivar,resolution
